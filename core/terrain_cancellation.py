"""Employee cancellation originals, independent of OWNER reconciliation decisions."""
from rest_framework import serializers
from .models import TerrainSubmission, TerrainOutcome
from .terrain_transport import StrictInput
from .terrain_application import BusinessConflict, validated
from .terrain_reversal import reverse_effect

SUPPORTED = {'VENTE_ANIMAUX', 'VENTE_OEUFS', 'DEPENSE', 'MORTALITE', 'DON', 'VOL'}


class CancellationInput(StrictInput):
    original_operation_id = serializers.UUIDField()
    reason = serializers.CharField(min_length=3, max_length=1000, trim_whitespace=True)
    lot_ref = serializers.JSONField(required=False)


def cancel(row, user):
    from .offline_permissions import has_fine_contract
    if not has_fine_contract(row.offline_authorization):
        raise BusinessConflict('CANCEL_PERMISSION_REQUIRED')
    data = validated(CancellationInput, row.payload)
    original = TerrainSubmission.objects.filter(exploitation_id=row.exploitation_id,
        client_operation_id=data['original_operation_id']).first()
    if not original or original.operation_type != 'CREATE' or original.entity_type != row.entity_type:
        raise BusinessConflict('CANCELLATION_ORIGINAL_INVALID')
    if not has_fine_contract(original.offline_authorization):
        raise BusinessConflict('CANCEL_LEGACY_ORIGINAL')
    # Employees correct their own originals; no implicit authority over a colleague.
    if original.author_user_id != row.author_user_id:
        raise BusinessConflict('CANCELLATION_OTHER_AUTHOR')
    if data.get('lot_ref') != original.payload.get('lot_ref'):
        raise BusinessConflict('CANCELLATION_ORIGINAL_INVALID')
    if row.business_occurred_at < original.business_occurred_at:
        raise BusinessConflict('CANCEL_DATE_BEFORE_ORIGINAL')
    outcome = TerrainOutcome.objects.select_for_update().get(submission=original)
    if outcome.business_status == 'SUPERSEDED':
        raise BusinessConflict('CANCEL_ALREADY_APPLIED')
    if outcome.business_status != 'CONFIRMED':
        raise BusinessConflict('CANCEL_ORIGINAL_NOT_CONFIRMED')
    # Existing reversal checks refuse paid sales and unresolved dependencies.
    from rest_framework.exceptions import ValidationError
    try:
        reverse_effect(original, outcome, {'decision_uuid': row.client_operation_id,
                                           'occurred_at':row.business_occurred_at})
    except ValidationError as error:
        detail = error.detail
        code = str(detail[0]) if isinstance(detail, list) and detail else 'CANCEL_REQUIRES_RESOLUTION'
        code = {'EXPENSE_HAS_ACTIVE_FEED_DECLARATION':'EXPENSE_HAS_ACTIVE_FEED',
                'BUSINESS_EFFECT_ALREADY_REVERSED':'CANCEL_ALREADY_APPLIED'}.get(code,code)
        raise BusinessConflict(code if len(code)<=30 else 'CANCEL_REQUIRES_RESOLUTION') from error
    from .models import Exploitation
    revision = Exploitation.objects.get(pk=row.exploitation_id).business_revision + 1
    outcome.server_version = f'farm:{revision}'
    outcome.save()
    row._terrain_lot_ids = outcome.affected_lot_ids
    return row


def register(handlers):
    from .business_authorization import guarded_handler
    from .permission_catalog import operation_action
    for kind in SUPPORTED:
        original_handler = handlers[kind]
        cancellation_handler = guarded_handler(operation_action(kind, 'REVERSE'), cancel)
        def dispatch(row, user, create=original_handler, reverse=cancellation_handler):
            return reverse(row, user) if getattr(row, 'operation_type', None) == 'REVERSE' else create(row, user)
        handlers[kind] = dispatch
