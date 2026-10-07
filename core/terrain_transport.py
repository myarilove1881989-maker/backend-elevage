"""Device-authenticated receipt, independent of the currently opened operator."""
import base64
import hashlib
import json
import re
import uuid
from datetime import timedelta
from cryptography.exceptions import InvalidSignature
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied, ValidationError
from .audit import ActorContext, audit_scope
from .device_services import load_public_key, verify_signature
from .models import (AuditEvent, DeviceRegistration, DeviceTransportChallenge,
    Exploitation, OfflineAuthorization, TerrainSubmission, TerrainOutcome, TerrainEntityMapping)

PATHS = {'RECEIVE': '/api/offline/submissions/', 'STATUS': '/api/offline/submissions/status/'}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
        default=lambda v: v.isoformat() if hasattr(v, 'isoformat') else str(v), allow_nan=False)


def reject_secrets(value, depth=0):
    if depth > 16:
        raise ValidationError('PAYLOAD_TOO_DEEP')
    if isinstance(value, dict):
        for key, child in value.items():
            if re.search(r'(^|_)(password|pin|jwt|token|private_key|refresh|secret|access|authorization)(_|$)',key,re.I):
                raise ValidationError('SECRETS_NOT_ALLOWED_IN_DECLARATION')
            reject_secrets(child, depth+1)
    elif isinstance(value, list):
        for child in value:
            reject_secrets(child, depth+1)


class StrictInput(serializers.Serializer):
    def to_internal_value(self, data):
        if not isinstance(data, dict) or set(data)-set(self.fields):
            raise ValidationError('UNKNOWN_DECLARATION_FIELDS')
        return super().to_internal_value(data)


class SubmissionInput(StrictInput):
    client_operation_id = serializers.UUIDField()
    local_sequence = serializers.IntegerField(min_value=1, max_value=9223372036854775807)
    author_user_id = serializers.IntegerField(min_value=1)
    author_membership_id = serializers.IntegerField(min_value=1)
    device_id = serializers.IntegerField(min_value=1)
    device_generation = serializers.IntegerField(min_value=1)
    exploitation_id = serializers.IntegerField(min_value=1)
    offline_authorization_id = serializers.UUIDField()
    entity_type = serializers.RegexField(r'^[A-Z][A-Z_]{0,31}$')
    operation_type = serializers.ChoiceField(choices=['CREATE', 'UPDATE', 'CANCEL', 'REVERSE'])
    local_entity_id = serializers.UUIDField(required=False, allow_null=True, default=None)
    payload = serializers.JSONField()
    dependencies = serializers.ListField(child=serializers.UUIDField(), max_length=50, default=list)
    expected_server_version = serializers.CharField(max_length=100, allow_blank=True, default='')
    business_occurred_at = serializers.DateTimeField()
    local_recorded_at = serializers.DateTimeField()

    def validate(self, data):
        if not isinstance(data['payload'], dict):
            raise ValidationError('OBJECT_PAYLOAD_REQUIRED')
        reject_secrets(data['payload'])
        if len(canonical(data['payload']).encode('utf-8')) > 65536:
            raise ValidationError('PAYLOAD_TOO_LARGE')
        if len(set(data['dependencies'])) != len(data['dependencies']):
            raise ValidationError('DUPLICATE_DEPENDENCY')
        return data


def message_for(request, challenge, device):
    return (f'ELEVAGE-DEVICE-TRANSPORT-V1\n{challenge.pk}\n{device.pk}\n'
        f'{device.exploitation_id}\n{device.write_generation}\n{challenge.purpose}\n'
        f'{request.method}\n{request.get_full_path()}\n'
        f'{hashlib.sha256(request.body).hexdigest()}').encode('utf-8')


def _active_device(device):
    farm = device.exploitation
    if (not farm.offline_policy_enabled or device.status != 'ACTIVE' or
        not device.is_primary_writer or device.write_generation != farm.write_generation):
        raise PermissionDenied('ACTIVE_PRIMARY_DEVICE_REQUIRED')


@transaction.atomic
def issue_transport_challenge(device_id, installation_uuid, purpose):
    try:
        device = DeviceRegistration.objects.select_related('exploitation').get(
            pk=device_id, installation_uuid=installation_uuid)
    except DeviceRegistration.DoesNotExist:
        raise PermissionDenied('ACTIVE_PRIMARY_DEVICE_REQUIRED')
    _active_device(device)
    now = timezone.now()
    DeviceTransportChallenge.objects.filter(device=device, expires_at__lt=now).delete()
    challenge = DeviceTransportChallenge.objects.create(device=device, purpose=purpose,
        expires_at=now+timedelta(minutes=5))
    return {'id':str(challenge.pk), 'device_id':device.pk, 'exploitation_id':device.exploitation_id,
        'device_generation':device.write_generation, 'purpose':purpose, 'method':'POST',
        'path':PATHS[purpose], 'signature_contract':'ELEVAGE-DEVICE-TRANSPORT-V1',
        'expires_at':challenge.expires_at.isoformat()}


def verify_transport(request, purpose):
    """Caller holds atomic transaction; farm lock serializes receipt/replacement."""
    try:
        device_id = int(request.headers.get('X-Elevage-Device',''))
        challenge_id = uuid.UUID(request.headers.get('X-Elevage-Challenge',''))
        signature = base64.b64decode(request.headers.get('X-Elevage-Signature',''), validate=True)
        farm_id = DeviceRegistration.objects.values_list('exploitation_id',flat=True).get(pk=device_id)
        farm = Exploitation.objects.select_for_update().get(pk=farm_id)
        device = DeviceRegistration.objects.select_for_update().get(pk=device_id, exploitation=farm)
        device.exploitation = farm
        challenge = DeviceTransportChallenge.objects.select_for_update().get(
            pk=challenge_id, device=device, purpose=purpose)
    except (ValueError, TypeError, DeviceRegistration.DoesNotExist,
            Exploitation.DoesNotExist, DeviceTransportChallenge.DoesNotExist):
        raise PermissionDenied('DEVICE_TRANSPORT_PROOF_INVALID')
    _active_device(device)
    if challenge.consumed_at or challenge.expires_at <= timezone.now():
        raise PermissionDenied('DEVICE_CHALLENGE_EXPIRED_OR_USED')
    if request.method != 'POST' or request.get_full_path() != PATHS[purpose]:
        raise PermissionDenied('DEVICE_TRANSPORT_PROOF_INVALID')
    try:
        verify_signature(load_public_key(device.public_key), signature, message_for(request,challenge,device))
    except (InvalidSignature, ValueError):
        raise PermissionDenied('DEVICE_TRANSPORT_PROOF_INVALID')
    challenge.consumed_at = timezone.now()
    challenge.save(update_fields=['consumed_at'])
    DeviceRegistration._base_manager.filter(pk=device.pk).update(last_seen_at=timezone.now())
    return device


def receipt(row):
    result = row.outcome
    from .cache_views import stock_snapshots
    data = {'client_operation_id':str(row.client_operation_id), 'transport_status':'SERVER_RECEIVED',
        'business_status':result.business_status, 'reason_code':result.reason_code,
        'reason_text':result.reason_text, 'author_user_id':row.author_user_id,
        'received_at':row.received_at.isoformat(), 'server_entity_type':result.server_entity_type,
        'server_entity_id':result.server_entity_id, 'server_version':result.server_version,
        'applied_at':result.applied_at.isoformat() if result.applied_at else None,
        'stock_snapshots':stock_snapshots(row.exploitation_id,result.affected_lot_ids),
        'entity_mappings':[{'entity_type':mapping.entity_type,
            'local_entity_id':str(mapping.local_entity_id),'server_entity_id':mapping.server_entity_id}
            for mapping in TerrainEntityMapping.objects.filter(submission=row)]}
    if row.entity_type == 'ENCAISSEMENT':
        from .models import EncaissementTerrain
        cash = EncaissementTerrain.objects.filter(submission=row).first()
        if cash:
            data['cash_recognition'] = {'montant_recu':str(cash.montant_recu),
                'montant_affecte':str(cash.montant_affecte),'montant_a_rapprocher':str(cash.montant_a_rapprocher),
                'payment_id':cash.payment_id,'mode':cash.mode}
    return data


@transaction.atomic
def receive(request, operations):
    device = verify_transport(request,'RECEIVE')
    results = []
    for data in operations:
        if (data['device_id'] != device.pk or data['exploitation_id'] != device.exploitation_id
                or data['device_generation'] != device.write_generation):
            raise PermissionDenied('DECLARATION_DEVICE_CONTEXT_MISMATCH')
        digest = hashlib.sha256(canonical(data).encode('utf-8')).hexdigest()
        previous = TerrainSubmission.objects.select_related('outcome').filter(
            exploitation_id=device.exploitation_id, client_operation_id=data['client_operation_id']).first()
        if previous:
            if previous.declaration_digest != digest:
                raise ValidationError('OPERATION_UUID_REUSED_WITH_DIFFERENT_DECLARATION')
            results.append(receipt(previous))
            continue
        if TerrainSubmission.objects.filter(device=device, local_sequence=data['local_sequence']).exists():
            raise ValidationError('LOCAL_SEQUENCE_REUSED')
        try:
            grant = OfflineAuthorization.objects.select_related('membership__user').get(
                pk=data['offline_authorization_id'], device=device,
                membership_id=data['author_membership_id'], membership__user_id=data['author_user_id'],
                membership__exploitation_id=device.exploitation_id,
                write_generation=device.write_generation)
        except OfflineAuthorization.DoesNotExist:
            raise PermissionDenied('ORIGINAL_AUTHORIZATION_CONTEXT_INVALID')
        member = grant.membership
        status, reason = 'UNREVIEWED', ''
        if (grant.revoked_at or not member.is_active or not member.user.is_active or
                member.version != grant.rights_version or member.role != 'OPERATEUR'):
            status, reason = 'NEEDS_RECONCILIATION', 'AUTHOR_RIGHTS_CHANGED'
        elif (not grant.capabilities.get('can_create_terrain_operation') or
              not grant.issued_at <= data['local_recorded_at'] <= grant.expires_at):
            status, reason = 'NEEDS_RECONCILIATION', 'OUTSIDE_ORIGINAL_AUTHORIZATION'
        elif data['client_operation_id'] in data['dependencies']:
            status, reason = 'NEEDS_RECONCILIATION', 'DEPENDENCY_CYCLE'
        elif data['dependencies']:
            status, reason = 'WAITING_DEPENDENCY', 'DEPENDENCY_NOT_CONFIRMED'
        fields = dict(data)
        fields['dependencies'] = [str(v) for v in fields['dependencies']]
        fields.pop('exploitation_id'); fields.pop('device_id'); fields.pop('offline_authorization_id')
        context = ActorContext(data['author_user_id'],device.exploitation_id,device.pk,'DEVICE',
            source='OFFLINE', operation_id=data['client_operation_id'],
            local_entity_id=data['local_entity_id'], business_occurred_at=data['business_occurred_at'])
        with audit_scope(context):
            row = TerrainSubmission.objects.create(exploitation=device.exploitation,device=device,
                offline_authorization=grant,declaration_digest=digest,**fields)
            TerrainOutcome.objects.create(submission=row,business_status=status,reason_code=reason)
            AuditEvent.objects.create(exploitation_id=device.exploitation_id,actor_user_id=row.author_user_id,
                device_id=device.pk,transport_identity='DEVICE',action='TERRAIN_RECEIVED',
                entity_type='core.terrainsubmission',entity_id=str(row.pk),operation_id=row.client_operation_id,
                local_entity_id=row.local_entity_id,business_occurred_at=row.business_occurred_at,
                source='OFFLINE',after_data={'declaration_digest':digest,'business_status':status})
        results.append(receipt(row))
    return device.pk, results


@transaction.atomic
def status_receipts(request, operation_ids):
    device = verify_transport(request,'STATUS')
    rows = TerrainSubmission.objects.select_related('outcome').filter(
        exploitation_id=device.exploitation_id, client_operation_id__in=operation_ids)
    return device.pk, [receipt(row) for row in rows]
