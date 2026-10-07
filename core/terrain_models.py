"""Immutable terrain declarations and separately mutable processing outcomes."""
import uuid
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from .audit import AuditedModel
from .foundation_models import AppendOnlyQuerySet


BUSINESS_STATES = (
    'UNREVIEWED', 'WAITING_DEPENDENCY', 'CONFIRMED', 'NEEDS_RECONCILIATION',
    'NOT_APPLIED', 'SUPERSEDED',
)


class DeviceTransportChallenge(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    device = models.ForeignKey('core.DeviceRegistration', on_delete=models.CASCADE)
    purpose = models.CharField(max_length=12, choices=[('RECEIVE', 'Receive'), ('STATUS', 'Status')])
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True)


class TerrainSubmission(models.Model):
    """Original author and payload survive conflicts, account changes and retries."""
    exploitation = models.ForeignKey('core.Exploitation', on_delete=models.PROTECT)
    client_operation_id = models.UUIDField()
    local_sequence = models.PositiveBigIntegerField()
    author_user_id = models.PositiveBigIntegerField()
    author_membership_id = models.PositiveBigIntegerField()
    device = models.ForeignKey('core.DeviceRegistration', on_delete=models.PROTECT)
    device_generation = models.PositiveIntegerField()
    offline_authorization = models.ForeignKey('core.OfflineAuthorization', on_delete=models.PROTECT)
    entity_type = models.CharField(max_length=32)
    operation_type = models.CharField(max_length=20)
    local_entity_id = models.UUIDField(null=True)
    payload = models.JSONField()
    dependencies = models.JSONField(default=list)
    expected_server_version = models.CharField(max_length=100, blank=True)
    business_occurred_at = models.DateTimeField()
    local_recorded_at = models.DateTimeField()
    declaration_digest = models.CharField(max_length=64)
    received_at = models.DateTimeField(default=timezone.now)
    objects = AppendOnlyQuerySet.as_manager()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['exploitation', 'client_operation_id'], name='terrain_farm_operation_unique'),
            models.UniqueConstraint(fields=['device', 'local_sequence'], name='terrain_device_sequence_unique'),
            models.CheckConstraint(condition=models.Q(local_sequence__gt=0), name='terrain_sequence_positive'),
        ]
        indexes = [models.Index(fields=['exploitation', 'received_at'], name='terrain_farm_received_idx')]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError('Terrain declarations are immutable.')
        kwargs['force_insert'] = True
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Terrain declarations are immutable.')


class TerrainOutcome(AuditedModel):
    submission = models.OneToOneField(TerrainSubmission, on_delete=models.PROTECT, related_name='outcome')
    business_status = models.CharField(max_length=24, default='UNREVIEWED', choices=[(s, s) for s in BUSINESS_STATES])
    reason_code = models.CharField(max_length=50, blank=True)
    reason_text = models.TextField(blank=True)
    server_entity_type = models.CharField(max_length=40, blank=True)
    server_entity_id = models.CharField(max_length=100, blank=True)
    server_version = models.CharField(max_length=100, blank=True)
    applied_at = models.DateTimeField(null=True)
    affected_lot_ids = models.JSONField(default=list)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(business_status__in=BUSINESS_STATES), name='terrain_business_status_valid')]


class TerrainEntityMapping(AuditedModel):
    exploitation = models.ForeignKey('core.Exploitation', on_delete=models.PROTECT)
    entity_type = models.CharField(max_length=32)
    local_entity_id = models.UUIDField()
    server_entity_id = models.PositiveBigIntegerField()
    submission = models.ForeignKey(TerrainSubmission, on_delete=models.PROTECT)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['exploitation', 'entity_type', 'local_entity_id'],
            name='terrain_farm_entity_mapping_unique')]


class EncaissementTerrain(AuditedModel):
    """Recognized cash receipt; allocation never rewrites the physical amount."""
    exploitation = models.ForeignKey('core.Exploitation', on_delete=models.PROTECT)
    submission = models.OneToOneField(TerrainSubmission, on_delete=models.PROTECT)
    client = models.ForeignKey('core.Client', on_delete=models.PROTECT)
    created_by = models.ForeignKey('core.User', on_delete=models.PROTECT)
    payment = models.OneToOneField('core.Payment', on_delete=models.PROTECT)
    business_occurred_at = models.DateTimeField()
    montant_recu = models.DecimalField(max_digits=12, decimal_places=2)
    montant_affecte = models.DecimalField(max_digits=12, decimal_places=2)
    montant_a_rapprocher = models.DecimalField(max_digits=12, decimal_places=2)
    mode = models.CharField(max_length=20, choices=[(s,s) for s in ('ESPECES','MOBILE_MONEY','VIREMENT','CHEQUE')])
    note = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(montant_recu__gt=0), name='terrain_cash_positive'),
            models.CheckConstraint(condition=models.Q(montant_affecte__gte=0, montant_a_rapprocher__gte=0), name='terrain_cash_parts_positive'),
            models.CheckConstraint(condition=models.Q(montant_recu=models.F('montant_affecte')+models.F('montant_a_rapprocher')), name='terrain_cash_parts_equal_received'),
        ]
