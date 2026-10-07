"""Explicit decisions preserve the original author, payload and physical receipts."""
import copy
import hashlib
from dataclasses import replace
from decimal import Decimal

from django.core.exceptions import ValidationError as ModelValidationError
from django.db import transaction
from django.db.models import Sum
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied, ValidationError

from .audit import audit_scope, snapshot, safe_data, record
from .models import (Exploitation, TerrainSubmission, TerrainOutcome,
    TerrainDecision, EncaissementTerrain, Lettrage, Vente, VenteOeufs, Task)
from .permissions import require_member
from .terrain_transport import StrictInput, canonical, reject_secrets, receipt
from .terrain_application import HANDLERS, BusinessConflict, context_for, _dependencies, TaskReportInput, validated
from .terrain_operations import map_entity, Reference, resolve


class AllocationInput(StrictInput):
    vente_ref = Reference()
    vente_type = serializers.ChoiceField(choices=['VENTE_ANIMAUX', 'VENTE_OEUFS'])
    montant = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal('0.01'))


class DecisionInput(StrictInput):
    decision_uuid = serializers.UUIDField()
    expected_decision_version = serializers.IntegerField(min_value=0)
    action = serializers.ChoiceField(choices=['APPLY_ORIGINAL', 'CANCEL', 'CORRECTION', 'CASH_ALLOCATION', 'REVERSE'])
    reason = serializers.CharField(min_length=3, max_length=10000, trim_whitespace=True)
    payload = serializers.JSONField(default=dict)
    expected_entity_version = serializers.CharField(max_length=100, required=False, allow_blank=True, default='')

    def validate(self, data):
        payload = data['payload']
        if not isinstance(payload, dict):
            raise ValidationError('OBJECT_PAYLOAD_REQUIRED')
        reject_secrets(payload)
        if len(canonical(payload).encode('utf-8')) > 65536:
            raise ValidationError('PAYLOAD_TOO_LARGE')
        if data['action'] in ('APPLY_ORIGINAL', 'CANCEL', 'REVERSE') and payload:
            raise ValidationError('UNEXPECTED_DECISION_PAYLOAD')
        if data['action'] == 'CASH_ALLOCATION':
            payload = AllocationInput().run_validation(payload)
        data['payload'] = safe_data(payload)
        return data


def require_decider(request):
    member = require_member(request.user)
    if not member.user.is_active:
        raise PermissionDenied('MEMBERSHIP_INACTIVE')
    is_owner = member.role == 'OWNER' and member.exploitation.proprietaire_id == request.user.pk
    if not is_owner and not member.can_reconcile:
        raise PermissionDenied('RECONCILIATION_PERMISSION_REQUIRED')
    return member


def decision_result(decision, row):
    return {'decision_uuid': str(decision.decision_uuid), 'decision_actor_id': decision.decision_actor_id,
        'original_author_id': row.author_user_id, 'action': decision.action, 'reason': decision.reason,
        'decision_version': row.outcome.decision_version, 'receipt': receipt(row)}


def apply_effective(row, outcome, farm, data):
    if row.entity_type == 'TASK' and data['action'] == 'CORRECTION' and outcome.applied_at is not None:
        values = validated(TaskReportInput,data['payload'])
        if values.pop('task_id') != row.payload.get('task_id'):
            raise ValidationError('CORRECTION_OUTSIDE_ORIGINAL_TASK')
        task = Task.objects.select_for_update().get(pk=row.payload['task_id'],exploitation_id=row.exploitation_id)
        if not data['expected_entity_version'] or data['expected_entity_version'] != str(task.version):
            raise ValidationError('TASK_VERSION_CONFLICT')
        if task.status == 'CANCELLED':
            raise ValidationError('TASK_OWNER_CANCELLATION_PRESERVED')
        task.status = values['status']
        if 'report' in values: task.report = values['report']
        task.completed_by_id = row.author_user_id if task.status == 'DONE' else None
        task.completed_at = row.business_occurred_at if task.status == 'DONE' else None
        task.version += 1; task.save()
        outcome.business_status = 'CONFIRMED'; outcome.reason_code = ''
        outcome.server_version = str(task.version)
        return data['payload']
    if outcome.applied_at is not None or outcome.business_status == 'CONFIRMED':
        raise ValidationError('ALREADY_APPLIED_USE_EXPLICIT_REVERSAL')
    if row.entity_type not in HANDLERS:
        raise ValidationError('BUSINESS_OPERATION_UNSUPPORTED')
    if not _dependencies(row):
        raise ValidationError('DEPENDENCIES_NOT_CONFIRMED')
    effective = copy.copy(row)
    if data['action'] == 'CORRECTION':
        if not data['payload']:
            raise ValidationError('CORRECTION_PAYLOAD_REQUIRED')
        effective.payload = copy.deepcopy(data['payload'])
        if row.entity_type == 'ENCAISSEMENT':
            for name in ('montant_recu', 'mode', 'note'):
                if effective.payload.get(name, '') != row.payload.get(name, ''):
                    raise ValidationError('PHYSICAL_CASH_ORIGIN_IMMUTABLE')
    if data['expected_entity_version']:
        effective.expected_server_version = data['expected_entity_version']
    user = copy.copy(row.offline_authorization.membership.user)
    # The original membership fixes the farm even if the user's current profile
    # moved later. No account, membership or device is reactivated or saved.
    user.exploitation = farm
    entity = HANDLERS[row.entity_type](effective, user)
    state = getattr(entity, '_terrain_business_status', 'CONFIRMED')
    if row.operation_type == 'CREATE' and row.entity_type != 'CLIENT' and state == 'CONFIRMED':
        map_entity(row, row.entity_type, entity)
    outcome.business_status = state
    outcome.reason_code = getattr(entity, '_terrain_reason_code', '')
    outcome.server_entity_type = row.entity_type
    outcome.server_entity_id = str(entity.pk)
    outcome.affected_lot_ids = getattr(entity, '_terrain_lot_ids', [])
    outcome.applied_at = timezone.now()
    outcome.server_version = str(getattr(entity, 'version', f'farm:{farm.business_revision}'))
    return effective.payload


def allocate_cash(row, outcome, data):
    if row.entity_type != 'ENCAISSEMENT' or outcome.applied_at is None:
        raise ValidationError('RECOGNIZED_CASH_REQUIRED')
    cash = EncaissementTerrain.objects.select_for_update().get(submission=row)
    actual_assigned = cash.payment.lettrages.aggregate(total=Sum('montant'))['total'] or Decimal('0.00')
    if actual_assigned != cash.montant_affecte:
        raise ValidationError('CASH_ALLOCATION_LEDGER_MISMATCH')
    values = AllocationInput().run_validation(data['payload'])
    kind = values['vente_type']
    sale = resolve(row, values['vente_ref'], kind, VenteOeufs if kind == 'VENTE_OEUFS' else Vente)
    if kind == 'VENTE_OEUFS':
        sale = Vente.objects.select_for_update().get(pk=sale.vente_id)
    if sale.client_id != cash.client_id:
        raise ValidationError('SALE_CLIENT_MISMATCH')
    amount = values['montant']
    if amount > cash.montant_a_rapprocher or amount > max(Decimal('0.00'), sale.reste_a_payer):
        raise ValidationError('ALLOCATION_EXCEEDS_RECEIPT_OR_DEBT')
    Lettrage.objects.create(vente=sale, payment=cash.payment, created_by_id=row.author_user_id, montant=amount)
    cash.montant_affecte += amount
    cash.montant_a_rapprocher -= amount
    cash.save(update_fields=['montant_affecte', 'montant_a_rapprocher'])
    outcome.business_status = 'NEEDS_RECONCILIATION' if cash.montant_a_rapprocher else 'CONFIRMED'
    outcome.reason_code = 'CASH_REMAINDER_REVIEW' if cash.montant_a_rapprocher else ''
    return data['payload']


@transaction.atomic
def decide(request, operation_uuid, data):
    member = require_decider(request)
    farm = Exploitation.objects.select_for_update().get(pk=member.exploitation_id)
    # Recheck authorization after the farm lock and prove a delegated tablet here,
    # so device replacement and business decisions use farm -> device lock order.
    member = require_decider(request)
    request.user.exploitation = farm
    if member.role != 'OWNER':
        from .device_services import verify_request_device
        request.verified_device = verify_request_device(request, primary=True)
    row = get_object_or_404(TerrainSubmission.objects.select_related(
        'offline_authorization__membership__user'), exploitation=farm, client_operation_id=operation_uuid)
    outcome = TerrainOutcome.objects.select_for_update().get(submission=row)
    row.outcome = outcome
    digest = hashlib.sha256(canonical({'operation_uuid':str(operation_uuid), **data}).encode('utf-8')).hexdigest()
    previous = TerrainDecision.objects.filter(exploitation=farm, decision_uuid=data['decision_uuid']).first()
    if previous:
        if previous.request_digest != digest or previous.decision_actor_id != request.user.pk:
            raise ValidationError('DECISION_UUID_REUSED')
        return decision_result(previous, row)
    if outcome.decision_version != data['expected_decision_version']:
        raise ValidationError('DECISION_VERSION_CONFLICT')
    before = snapshot(outcome)
    context = replace(context_for(row), decision_actor_id=request.user.pk,
        transport_identity='USER', source='RECONCILE')
    with audit_scope(context, reason_code=data['action'], reason_text=data['reason']):
        farm.business_revision += 1
        effective_payload = {}
        try:
            with transaction.atomic():
                if data['action'] in ('APPLY_ORIGINAL', 'CORRECTION'):
                    effective_payload = apply_effective(row, outcome, farm, data)
                elif data['action'] == 'CASH_ALLOCATION':
                    effective_payload = allocate_cash(row, outcome, data)
                elif data['action'] == 'REVERSE':
                    from .terrain_reversal import reverse_effect
                    effective_payload = reverse_effect(row, outcome, data)
                elif data['action'] == 'CANCEL':
                    if outcome.applied_at is not None or outcome.business_status == 'CONFIRMED':
                        raise ValidationError('APPLIED_OPERATION_REQUIRES_REVERSAL')
                    outcome.business_status = 'NOT_APPLIED'
                    outcome.reason_code = 'CANCELLED_BY_DECISION'
        except BusinessConflict as error:
            raise ValidationError(error.code)
        except ModelValidationError:
            raise ValidationError('BUSINESS_VALIDATION_REQUIRED')
        farm.save(update_fields=['business_revision'])
        outcome.decision_version += 1
        outcome.reason_text = data['reason']
        if row.entity_type != 'TASK' or data['action'] not in ('APPLY_ORIGINAL', 'CORRECTION'):
            outcome.server_version = f'farm:{farm.business_revision}'
        outcome.save()
        decision = TerrainDecision.objects.create(exploitation=farm, submission=row,
            decision_uuid=data['decision_uuid'], decision_actor_id=request.user.pk, action=data['action'],
            reason=data['reason'], effective_payload=effective_payload, request_digest=digest,
            before_data=before, after_data=snapshot(outcome))
        record(row, data['action'], before, snapshot(outcome), category='RECONCILIATION')
    return decision_result(decision, row)
