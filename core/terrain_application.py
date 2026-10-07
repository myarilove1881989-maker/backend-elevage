"""Apply already received originals in a separate transaction, never erase receipt."""
from django.core.exceptions import ValidationError as ModelValidationError
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers
from .audit import ActorContext, audit_scope
from .models import (AuditEvent, Client, Task, Exploitation, DeviceRegistration,
    TerrainSubmission, TerrainOutcome, TerrainEntityMapping)


class BusinessConflict(Exception):
    def __init__(self, code):
        self.code = code


class ClientInput(serializers.ModelSerializer):
    class Meta:
        model = Client
        fields = ('nom', 'telephone', 'pays', 'ville')


class TaskReportInput(serializers.Serializer):
    task_id = serializers.IntegerField(min_value=1)
    status = serializers.ChoiceField(choices=['TODO', 'IN_PROGRESS', 'DONE'])
    report = serializers.CharField(max_length=10000, allow_blank=True, required=False)


def validated(serializer, payload):
    instance = serializer(data=payload)
    if set(payload)-set(instance.fields):
        raise BusinessConflict('UNKNOWN_BUSINESS_FIELDS')
    instance.is_valid(raise_exception=True)
    return instance.validated_data


def _client(row, user):
    if row.operation_type != 'CREATE' or not row.local_entity_id:
        raise BusinessConflict('CLIENT_OPERATION_UNSUPPORTED')
    data = validated(ClientInput, row.payload)
    if TerrainEntityMapping.objects.filter(exploitation_id=row.exploitation_id,
            entity_type='CLIENT', local_entity_id=row.local_entity_id).exists():
        raise BusinessConflict('LOCAL_ENTITY_ALREADY_MAPPED')
    client = Client.objects.create(exploitation_id=row.exploitation_id, created_by=user, **data)
    TerrainEntityMapping.objects.create(exploitation_id=row.exploitation_id, entity_type='CLIENT',
        local_entity_id=row.local_entity_id, server_entity_id=client.pk, submission=row)
    return client


def _task(row, user):
    if row.operation_type != 'UPDATE':
        raise BusinessConflict('TASK_OWNER_PLANNING_ONLY')
    data = validated(TaskReportInput, row.payload)
    try:
        task = Task.objects.select_for_update().get(pk=data.pop('task_id'), exploitation_id=row.exploitation_id)
    except Task.DoesNotExist:
        raise BusinessConflict('TASK_NOT_AVAILABLE')
    if task.assigned_to_id not in (None, user.pk):
        raise BusinessConflict('TASK_OTHER_ASSIGNEE')
    if row.expected_server_version != str(task.version):
        raise BusinessConflict('TASK_VERSION_CONFLICT')
    allowed = {'TODO': {'TODO', 'IN_PROGRESS'}, 'IN_PROGRESS': {'IN_PROGRESS', 'DONE'},
        'DONE': {'DONE'}, 'CANCELLED': set()}
    status = data['status']
    if status not in allowed[task.status]:
        raise BusinessConflict('TASK_TRANSITION_CONFLICT')
    if status == 'DONE' and task.status != 'DONE':
        task.completed_by = user
        task.completed_at = row.business_occurred_at
    task.status = status
    if 'report' in data:
        task.report = data['report']
    task.version += 1
    task.save()
    return task


HANDLERS = {'CLIENT': _client, 'TASK': _task}


def context_for(row):
    return ActorContext(row.author_user_id, row.exploitation_id, row.device_id, 'DEVICE',
        source='OFFLINE', operation_id=row.client_operation_id, local_entity_id=row.local_entity_id,
        business_occurred_at=row.business_occurred_at)


def _dependencies(row):
    dependencies = TerrainSubmission.objects.filter(exploitation_id=row.exploitation_id,
        client_operation_id__in=row.dependencies).select_related('outcome')
    if dependencies.count() != len(row.dependencies):
        return False
    states = [item.outcome.business_status for item in dependencies]
    if any(state in ('NEEDS_RECONCILIATION', 'NOT_APPLIED', 'SUPERSEDED') for state in states):
        raise BusinessConflict('DEPENDENCY_NOT_APPLIED')
    return all(state == 'CONFIRMED' for state in states)


@transaction.atomic
def process_pending(device_id):
    farm_id = DeviceRegistration.objects.values_list('exploitation_id', flat=True).get(pk=device_id)
    farm = Exploitation.objects.select_for_update().get(pk=farm_id)
    device = DeviceRegistration.objects.select_for_update().get(pk=device_id)
    if (not farm.offline_policy_enabled or device.status != 'ACTIVE' or
            not device.is_primary_writer or device.write_generation != farm.write_generation):
        return
    # A bounded pass retries an earlier dependency after its later-arriving parent.
    for _ in range(3):
        progress = False
        outcomes = TerrainOutcome.objects.select_for_update(of=('self',)).filter(
            submission__exploitation_id=farm_id, business_status__in=['UNREVIEWED', 'WAITING_DEPENDENCY'],
            submission__entity_type__in=HANDLERS).select_related(
                'submission__offline_authorization__membership__user').order_by('submission__local_sequence')[:200]
        for result in outcomes:
            row = result.submission
            grant = row.offline_authorization
            member = grant.membership
            code = ''
            try:
                if (grant.revoked_at or not member.is_active or not member.user.is_active or
                        member.version != grant.rights_version or member.role != 'OPERATEUR'):
                    raise BusinessConflict('AUTHOR_RIGHTS_CHANGED')
                if row.device_id != device.pk or row.device_generation != farm.write_generation:
                    raise BusinessConflict('DEVICE_GENERATION_CHANGED')
                if not _dependencies(row):
                    continue
                # Business failure rolls back only this application savepoint.
                with transaction.atomic(), audit_scope(context_for(row)):
                    entity = HANDLERS[row.entity_type](row, member.user)
                    result.business_status = 'CONFIRMED'
                    result.reason_code = result.reason_text = ''
                    result.server_entity_type = row.entity_type
                    result.server_entity_id = str(entity.pk)
                    result.server_version = str(getattr(entity, 'version', ''))
                    result.applied_at = timezone.now()
                    result.save()
                    AuditEvent.objects.create(exploitation_id=farm_id, actor_user_id=row.author_user_id,
                        device_id=row.device_id, transport_identity='DEVICE', source='OFFLINE',
                        action='TERRAIN_APPLIED', entity_type=entity._meta.label_lower, entity_id=str(entity.pk),
                        operation_id=row.client_operation_id, local_entity_id=row.local_entity_id,
                        business_occurred_at=row.business_occurred_at, applied_at=result.applied_at,
                        after_data={'business_status':'CONFIRMED'})
                progress = True
            except BusinessConflict as error:
                code = error.code
            except (serializers.ValidationError, ModelValidationError):
                code = 'BUSINESS_VALIDATION_REQUIRED'
            if code:
                result.refresh_from_db()
                with audit_scope(context_for(row), reason_code=code):
                    result.business_status = 'NEEDS_RECONCILIATION'
                    result.reason_code = code
                    result.save()
                progress = True
        if not progress:
            break


def current_receipts(device_id, operation_ids):
    from .terrain_transport import receipt
    farm_id = DeviceRegistration.objects.values_list('exploitation_id', flat=True).get(pk=device_id)
    rows = TerrainSubmission.objects.filter(exploitation_id=farm_id,
        client_operation_id__in=operation_ids).select_related('outcome')
    by_id = {str(row.client_operation_id): receipt(row) for row in rows}
    return [by_id[str(key)] for key in operation_ids if str(key) in by_id]
