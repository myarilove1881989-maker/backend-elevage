"""Physical supplies foundation. No financial values and no mutable balance."""
import uuid
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone
from .audit import AuditedModel


class SupplyAppendOnlyQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError('SUPPLY_IMMUTABLE')

    def delete(self):
        raise ValidationError('SUPPLY_IMMUTABLE')

    def bulk_update(self, *args, **kwargs):
        raise ValidationError('SUPPLY_IMMUTABLE')

    def bulk_create(self, *args, **kwargs):
        raise ValidationError('SUPPLY_SERVICE_REQUIRED')


class SupplyFact(AuditedModel):
    objects = SupplyAppendOnlyQuerySet.as_manager()

    class Meta:
        abstract = True
        base_manager_name = 'objects'

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError('SUPPLY_IMMUTABLE')
        self.full_clean()
        kwargs['force_insert'] = True
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('SUPPLY_IMMUTABLE')


class SupplyProduct(AuditedModel):
    """Stable UUID; renaming never changes stock identity."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    exploitation = models.ForeignKey('core.Exploitation', on_delete=models.PROTECT)
    kind = models.CharField(max_length=8, choices=[('FEED', 'Feed'), ('VACCINE', 'Vaccine')])
    name = models.CharField(max_length=120)
    alert_threshold = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    archived = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(kind__in=['FEED', 'VACCINE']), name='supply_product_kind'),
            models.CheckConstraint(condition=Q(alert_threshold__gte=0), name='supply_threshold_nonnegative'),
        ]

    def clean(self):
        if not self.name.strip():
            raise ValidationError('SUPPLY_NAME_REQUIRED')
        if self.kind == 'VACCINE' and self.alert_threshold % 1:
            raise ValidationError('SUPPLY_INTEGER_DOSES_REQUIRED')
        if not self._state.adding:
            prior = type(self).objects.get(pk=self.pk)
            if (prior.exploitation_id, prior.kind) != (self.exploitation_id, self.kind):
                raise ValidationError('SUPPLY_PRODUCT_IDENTITY_IMMUTABLE')

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class SupplyOperation(SupplyFact):
    exploitation = models.ForeignKey('core.Exploitation', on_delete=models.PROTECT)
    operation_uuid = models.UUIDField()
    author = models.ForeignKey('core.User', on_delete=models.PROTECT)
    kind = models.CharField(max_length=24)
    payload = models.JSONField()
    fingerprint = models.CharField(max_length=64)
    reason = models.TextField(blank=True)
    occurred_at = models.DateTimeField()
    recorded_at = models.DateTimeField(default=timezone.now)
    reverses = models.OneToOneField('self', null=True, blank=True, on_delete=models.PROTECT,
                                   related_name='reversal')

    class Meta(SupplyFact.Meta):
        constraints = [models.UniqueConstraint(fields=['exploitation', 'operation_uuid'], name='supply_operation_once')]

    def clean(self):
        if self.author.exploitation_id != self.exploitation_id:
            raise ValidationError('SUPPLY_OUTSIDE_FARM')
        if self.reverses_id and self.reverses.exploitation_id != self.exploitation_id:
            raise ValidationError('SUPPLY_OUTSIDE_FARM')


class SupplyReceipt(SupplyFact):
    operation = models.OneToOneField(SupplyOperation, on_delete=models.PROTECT)
    product = models.ForeignKey(SupplyProduct, on_delete=models.PROTECT)
    expense = models.ForeignKey('core.Depense', null=True, blank=True, on_delete=models.PROTECT,
                               related_name='physical_supply_receipts')
    manufacturer_batch = models.CharField(max_length=120, blank=True)
    expires_on = models.DateField(null=True, blank=True)
    vial_count = models.PositiveIntegerField(null=True, blank=True)
    doses_per_vial = models.PositiveIntegerField(null=True, blank=True)
    quantity = models.DecimalField(max_digits=14, decimal_places=3)

    class Meta(SupplyFact.Meta):
        constraints = [
            models.CheckConstraint(condition=Q(quantity__gt=0), name='supply_receipt_positive'),
            models.UniqueConstraint(fields=['expense'], condition=Q(expense__isnull=False), name='supply_expense_receipt_once'),
        ]

    def clean(self):
        farm = self.operation.exploitation_id
        if self.product.exploitation_id != farm or (self.expense_id and self.expense.lot.exploitation_id != farm):
            raise ValidationError('SUPPLY_OUTSIDE_FARM')
        if self.product.kind == 'VACCINE':
            if not self.expires_on or not self.manufacturer_batch.strip() or not self.vial_count or not self.doses_per_vial:
                raise ValidationError('SUPPLY_VACCINE_RECEIPT_REQUIRED')
            if self.quantity != self.vial_count * self.doses_per_vial:
                raise ValidationError('SUPPLY_DOSE_TOTAL_MISMATCH')


class SupplyVial(SupplyFact):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    receipt = models.ForeignKey(SupplyReceipt, on_delete=models.PROTECT, related_name='vials')
    ordinal = models.PositiveIntegerField()

    class Meta(SupplyFact.Meta):
        constraints = [models.UniqueConstraint(fields=['receipt', 'ordinal'], name='supply_vial_ordinal_unique')]

    def clean(self):
        if self.receipt.product.kind != 'VACCINE' or not 1 <= self.ordinal <= self.receipt.vial_count:
            raise ValidationError('SUPPLY_VIAL_INVALID')


class SupplyVialOpening(SupplyFact):
    vial = models.OneToOneField(SupplyVial, on_delete=models.PROTECT, related_name='opening')
    operation = models.OneToOneField(SupplyOperation, on_delete=models.PROTECT)
    opened_at = models.DateTimeField()
    usable_seconds = models.PositiveIntegerField(null=True, blank=True)

    def clean(self):
        if self.vial.receipt.operation.exploitation_id != self.operation.exploitation_id:
            raise ValidationError('SUPPLY_OUTSIDE_FARM')
        if self.usable_seconds is not None and self.usable_seconds <= 0:
            raise ValidationError('SUPPLY_OPEN_DURATION_INVALID')


class SupplyMovement(SupplyFact):
    operation = models.ForeignKey(SupplyOperation, on_delete=models.PROTECT, related_name='movements')
    line = models.PositiveIntegerField()
    product = models.ForeignKey(SupplyProduct, on_delete=models.PROTECT)
    species = models.ForeignKey('core.Espece', on_delete=models.PROTECT)
    lot = models.ForeignKey('core.Lot', null=True, blank=True, on_delete=models.PROTECT)
    destination_lot = models.ForeignKey('core.Lot', null=True, blank=True, on_delete=models.PROTECT,
                                        related_name='received_supply_movements')
    vial = models.ForeignKey(SupplyVial, null=True, blank=True, on_delete=models.PROTECT)
    kind = models.CharField(max_length=16, choices=[(x, x) for x in
        ['ENTRY', 'DISTRIBUTION', 'TRANSFER', 'LOSS', 'CORRECTION', 'ADMINISTRATION', 'REVERSAL']])
    quantity = models.DecimalField(max_digits=14, decimal_places=3)

    class Meta(SupplyFact.Meta):
        constraints = [
            models.UniqueConstraint(fields=['operation', 'line'], name='supply_movement_once'),
            models.CheckConstraint(condition=~Q(quantity=0), name='supply_movement_nonzero'),
            models.CheckConstraint(condition=Q(kind__in=['ENTRY','DISTRIBUTION','TRANSFER','LOSS','CORRECTION','ADMINISTRATION','REVERSAL']), name='supply_movement_kind'),
            models.CheckConstraint(condition=Q(kind__in=['TRANSFER','CORRECTION','REVERSAL']) | Q(kind='ENTRY',quantity__gt=0) | Q(kind__in=['DISTRIBUTION','LOSS','ADMINISTRATION'],quantity__lt=0), name='supply_movement_sign'),
        ]
        indexes = [models.Index(fields=['product','species','lot'], name='supply_position_idx')]

    def clean(self):
        farm = self.operation.exploitation_id
        if self.product.exploitation_id != farm or self.species.exploitation_id != farm:
            raise ValidationError('SUPPLY_OUTSIDE_FARM')
        if self.lot_id and (self.lot.exploitation_id != farm or self.lot.espece_id != self.species_id):
            raise ValidationError('SUPPLY_LOT_SPECIES_MISMATCH')
        if self.destination_lot_id and (self.destination_lot.exploitation_id != farm or self.destination_lot.espece_id != self.species_id):
            raise ValidationError('SUPPLY_LOT_SPECIES_MISMATCH')
        if self.product.kind == 'VACCINE':
            if not self.vial_id or self.vial.receipt.product_id != self.product_id or self.quantity % 1:
                raise ValidationError('SUPPLY_VIAL_DOSES_REQUIRED')
        elif self.vial_id or self.kind == 'ADMINISTRATION':
            raise ValidationError('SUPPLY_FEED_MOVEMENT_INVALID')
