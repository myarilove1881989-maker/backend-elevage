"""Models imported by core.models; numeric snapshot IDs keep audit history intact."""
import json
import uuid
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from .audit import AuditedModel, safe_data


class ExploitationMembership(AuditedModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='memberships')
    exploitation = models.ForeignKey('core.Exploitation', on_delete=models.CASCADE, related_name='memberships')
    role = models.CharField(max_length=12, choices=[('OWNER', 'Owner'), ('OPERATEUR', 'Operator')])
    is_active = models.BooleanField(default=True)
    can_reconcile = models.BooleanField(default=False)
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    disabled_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                   null=True, blank=True, related_name='+')
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                   null=True, blank=True, related_name='+')

    class Meta:
        constraints = [models.UniqueConstraint(fields=['user', 'exploitation'], name='membership_user_tenant_unique')]


class DeviceRegistration(AuditedModel):
    installation_uuid = models.UUIDField(unique=True)
    exploitation = models.ForeignKey('core.Exploitation', on_delete=models.CASCADE, related_name='devices')
    display_name = models.CharField(max_length=100)
    platform = models.CharField(max_length=20, default='ANDROID', choices=[('ANDROID', 'Android')])
    public_key = models.TextField()
    status = models.CharField(max_length=12, default='PENDING', choices=[
        ('PENDING', 'Pending'), ('ACTIVE', 'Active'), ('REVOKED', 'Revoked')])
    is_primary_writer = models.BooleanField(default=False)
    write_generation = models.PositiveIntegerField(default=1)
    registered_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    last_full_sync_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revocation_reason = models.CharField(max_length=255, blank=True)
    activated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')
    revoked_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['exploitation'], condition=models.Q(status='ACTIVE', is_primary_writer=True),
                                    name='one_active_primary_device'),
            models.CheckConstraint(condition=models.Q(is_primary_writer=False) | models.Q(status='ACTIVE'),
                                   name='primary_device_must_be_active'),
        ]


class DeviceChallenge(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    device = models.ForeignKey(DeviceRegistration, on_delete=models.CASCADE)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    purpose = models.CharField(max_length=20)
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True, blank=True)


class OfflineAuthorization(AuditedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    membership = models.ForeignKey(ExploitationMembership, on_delete=models.PROTECT, related_name='offline_authorizations')
    device = models.ForeignKey(DeviceRegistration, on_delete=models.PROTECT, related_name='offline_authorizations')
    capabilities = models.JSONField(default=dict)
    rights_version = models.PositiveIntegerField()
    write_generation = models.PositiveIntegerField()
    issued_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)


class AppendOnlyQuerySet(models.QuerySet):
    def bulk_create(self, *args, **kwargs):
        raise ValidationError('Audit events must be created through validated save().')

    def update(self, **kwargs):
        raise ValidationError('Audit events are append-only.')

    def delete(self):
        raise ValidationError('Audit events are append-only.')

    def bulk_update(self, *args, **kwargs):
        raise ValidationError('Audit events are append-only.')


class AuditEvent(models.Model):
    # Deliberately not FK: deleting an account/object must not rewrite history.
    exploitation_id = models.BigIntegerField(db_index=True)
    actor_user_id = models.BigIntegerField(null=True)
    decision_actor_id = models.BigIntegerField(null=True)
    device_id = models.BigIntegerField(null=True)
    transport_identity = models.CharField(max_length=30, default='USER')
    category = models.CharField(max_length=20, default='BUSINESS')
    action = models.CharField(max_length=40)
    entity_type = models.CharField(max_length=100)
    entity_id = models.CharField(max_length=100)
    local_entity_id = models.UUIDField(null=True)
    operation_id = models.UUIDField(null=True)
    correlation_id = models.UUIDField(default=uuid.uuid4)
    before_data = models.JSONField(default=dict)
    after_data = models.JSONField(default=dict)
    business_occurred_at = models.DateTimeField(null=True)
    received_at = models.DateTimeField(default=timezone.now)
    applied_at = models.DateTimeField(null=True)
    reason_code = models.CharField(max_length=30, blank=True)
    reason_text = models.TextField(blank=True)
    source = models.CharField(max_length=10, default='ONLINE')
    metadata = models.JSONField(default=dict)
    objects = AppendOnlyQuerySet.as_manager()

    class Meta:
        ordering = ['-received_at', '-id']
        indexes = [models.Index(fields=['exploitation_id', 'received_at'], name='audit_tenant_received_idx'),
                   models.Index(fields=['exploitation_id', 'entity_type', 'entity_id'], name='audit_tenant_entity_idx')]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError('Audit events are append-only.')
        for field in ('before_data', 'after_data', 'metadata'):
            value = safe_data(getattr(self, field))
            if len(json.dumps(value)) > 65536:
                raise ValidationError('Audit payload exceeds 64 KiB.')
            setattr(self, field, value)
        kwargs['force_insert'] = True
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Audit events are append-only.')
