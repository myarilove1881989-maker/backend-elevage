"""Separate aquatic positions; no terrestrial Lot or financial mirror."""
import uuid
from django.db import models
from django.conf import settings
from django.core.exceptions import ValidationError
from core.audit import AuditedModel


class TenantEntity(AuditedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    exploitation = models.ForeignKey('core.Exploitation', on_delete=models.PROTECT)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        abstract = True

    def clean(self):
        super().clean()
        for name in ('species','stage','cohort','installation'):
            if getattr(self,name+'_id',None):
                related=getattr(self,name)
                if related.exploitation_id!=self.exploitation_id:
                    raise ValidationError('AQUATIC_REFERENCE_OUTSIDE_FARM')
        if isinstance(self,Cohort) and self.stage_id and self.stage.species_id!=self.species_id:
            raise ValidationError('STAGE_SPECIES_MISMATCH')

    def save(self,*args,**kwargs):
        self.clean()
        return super().save(*args,**kwargs)


class Activity(TenantEntity):
    enabled = models.BooleanField(default=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['exploitation'], name='aquatic_activity_farm')]


class Installation(TenantEntity):
    code = models.CharField(max_length=50)
    name = models.CharField(max_length=120)
    type = models.CharField(max_length=8, choices=[(v,v) for v in ('BASSIN','ETANG','CAGE','BAC')])
    system_code = models.CharField(max_length=80, blank=True)
    capacity = models.DecimalField(max_digits=18, decimal_places=3, null=True, blank=True)
    capacity_unit = models.CharField(max_length=12, blank=True)
    location = models.CharField(max_length=200, blank=True)
    status = models.CharField(max_length=8, default='ACTIVE', choices=[('ACTIVE','Active'),('INACTIVE','Inactive')])

    class Meta:
        constraints = [models.UniqueConstraint(fields=['exploitation','code'], name='aquatic_installation_code'),
            models.CheckConstraint(condition=(models.Q(capacity__isnull=True,capacity_unit='') |
                (models.Q(capacity__gt=0) & ~models.Q(capacity_unit=''))), name='aquatic_capacity_pair')]
        indexes = [models.Index(fields=['exploitation','status'])]


class AquaticSpeciesProfile(TenantEntity):
    species = models.OneToOneField('core.Espece', on_delete=models.PROTECT)
    active = models.BooleanField(default=True)


class Stage(TenantEntity):
    species = models.ForeignKey('core.Espece', on_delete=models.PROTECT)
    code = models.CharField(max_length=50)
    name = models.CharField(max_length=100)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['exploitation','species','code'], name='aquatic_stage_code')]


class Cohort(TenantEntity):
    code = models.CharField(max_length=50)
    name = models.CharField(max_length=120)
    species = models.ForeignKey('core.Espece', on_delete=models.PROTECT)
    stage = models.ForeignKey(Stage, null=True, blank=True, on_delete=models.PROTECT)
    origin = models.JSONField()
    started_at = models.DateTimeField()
    status = models.CharField(max_length=6, default='OPEN', choices=[('OPEN','Open'),('CLOSED','Closed')])

    class Meta:
        constraints = [models.UniqueConstraint(fields=['exploitation','code'], name='aquatic_cohort_code')]
        indexes = [models.Index(fields=['exploitation','species','status'])]


class Position(TenantEntity):
    cohort = models.ForeignKey(Cohort, on_delete=models.PROTECT, related_name='positions')
    installation = models.ForeignKey(Installation, on_delete=models.PROTECT, related_name='positions')
    gross_count = models.BigIntegerField(default=0)
    # Phase 2 hook: no public mutation or invented distribution of unknown deaths.
    reserved_count = models.BigIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['cohort','installation'], name='aquatic_position_unique'),
            models.CheckConstraint(condition=models.Q(gross_count__gte=0,reserved_count__gte=0) &
                models.Q(gross_count__gte=models.F('reserved_count')), name='aquatic_position_nonnegative')]


class ImmutableQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError('AQUATIC_FACT_IMMUTABLE')

    def delete(self):
        raise ValidationError('AQUATIC_FACT_IMMUTABLE')

    def bulk_update(self, *args, **kwargs):
        raise ValidationError('AQUATIC_FACT_IMMUTABLE')


class ImmutableFact(AuditedModel):
    objects = ImmutableQuerySet.as_manager()

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError('AQUATIC_FACT_IMMUTABLE')
        if isinstance(self,Event) and self.cohort.exploitation_id!=self.exploitation_id:
            raise ValidationError('AQUATIC_REFERENCE_OUTSIDE_FARM')
        if isinstance(self,CountLine) and (self.event.exploitation_id!=self.position.exploitation_id or self.event.cohort_id!=self.position.cohort_id):
            raise ValidationError('AQUATIC_REFERENCE_OUTSIDE_FARM')
        for name in ('cohort','installation','species','stage','supersedes','parent','reverses'):
            if getattr(self,name+'_id',None) and hasattr(self,'exploitation_id'):
                if getattr(self,name).exploitation_id!=self.exploitation_id:
                    raise ValidationError('AQUATIC_REFERENCE_OUTSIDE_FARM')
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('AQUATIC_FACT_IMMUTABLE')


class Command(ImmutableFact):
    exploitation = models.ForeignKey('core.Exploitation', on_delete=models.PROTECT)
    operation_uuid = models.UUIDField()
    schema_version = models.PositiveIntegerField(default=1)
    kind = models.CharField(max_length=40)
    fingerprint = models.CharField(max_length=64)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    received_at = models.DateTimeField(auto_now_add=True)
    result = models.JSONField()
    response_status = models.PositiveIntegerField(default=201)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['exploitation','operation_uuid'], name='aquatic_command_once')]


class Event(ImmutableFact):
    # Internal sequence provides a deterministic tie break; API exposes UUID.
    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    exploitation = models.ForeignKey('core.Exploitation', on_delete=models.PROTECT)
    command_uuid = models.UUIDField()
    cohort = models.ForeignKey(Cohort, on_delete=models.PROTECT)
    type = models.CharField(max_length=16)
    occurred_at = models.DateTimeField()
    recorded_at = models.DateTimeField(auto_now_add=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    reason = models.CharField(max_length=500)

    class Meta:
        indexes = [models.Index(fields=['exploitation','occurred_at','id'])]


class CountLine(ImmutableFact):
    event = models.ForeignKey(Event, on_delete=models.PROTECT, related_name='lines')
    position = models.ForeignKey(Position, on_delete=models.PROTECT)
    delta_count = models.BigIntegerField()
    line_no = models.PositiveIntegerField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=['event','line_no'], name='aquatic_event_line'),
            models.CheckConstraint(condition=~models.Q(delta_count=0), name='aquatic_line_nonzero')]


class Observation(ImmutableFact):
    """Immutable technical fact; payload contains reproducible exact ratios/context."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    exploitation = models.ForeignKey('core.Exploitation', on_delete=models.PROTECT)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    operation_uuid = models.UUIDField()
    kind = models.CharField(max_length=8, choices=[('WEIGHT','Weight'),('WATER','Water')])
    cohort = models.ForeignKey(Cohort, null=True, blank=True, on_delete=models.PROTECT)
    installation = models.ForeignKey(Installation, null=True, blank=True, on_delete=models.PROTECT)
    occurred_at = models.DateTimeField()
    recorded_at = models.DateTimeField(auto_now_add=True)
    sample_count = models.PositiveIntegerField(null=True, blank=True)
    mass_kg = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    direct_mean_g = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    temperature_c = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    ph = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    oxygen_mg_l = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    note = models.CharField(max_length=500, blank=True)
    result = models.JSONField()

    class Meta:
        indexes = [models.Index(fields=['exploitation','kind','occurred_at','id'])]
        constraints = [
            models.CheckConstraint(name='aquatic_observation_target', condition=(
                models.Q(kind='WATER',installation__isnull=False,cohort__isnull=True) |
                (models.Q(kind='WEIGHT') & (
                    models.Q(installation__isnull=False,cohort__isnull=True) |
                    models.Q(installation__isnull=True,cohort__isnull=False))))),
            models.CheckConstraint(name='aquatic_observation_values', condition=(
                (models.Q(kind='WATER',sample_count__isnull=True,mass_kg__isnull=True,direct_mean_g__isnull=True) &
                 (models.Q(temperature_c__isnull=False)|models.Q(ph__isnull=False)|models.Q(oxygen_mg_l__isnull=False))) |
                (models.Q(kind='WEIGHT',temperature_c__isnull=True,ph__isnull=True,oxygen_mg_l__isnull=True) & (
                    models.Q(sample_count__isnull=False,mass_kg__isnull=False,sample_count__gt=0,mass_kg__gt=0,direct_mean_g__isnull=True) |
                    models.Q(sample_count__isnull=True,mass_kg__isnull=True,direct_mean_g__isnull=False,direct_mean_g__gt=0)))))
        ]


class ObservationRule(ImmutableFact):
    """Dated, immutable rule. Overlapping intervals for the same key are refused."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    exploitation = models.ForeignKey('core.Exploitation', on_delete=models.PROTECT)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    operation_uuid = models.UUIDField()
    supersedes = models.OneToOneField('self', null=True, blank=True, on_delete=models.PROTECT, related_name='successor')
    parameter = models.CharField(max_length=20)
    scope = models.CharField(max_length=16)
    installation = models.ForeignKey(Installation, null=True, blank=True, on_delete=models.PROTECT)
    species = models.ForeignKey('core.Espece', null=True, blank=True, on_delete=models.PROTECT)
    stage = models.ForeignKey(Stage, null=True, blank=True, on_delete=models.PROTECT)
    system_code = models.CharField(max_length=80, blank=True)
    lower = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    upper = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    valid_from = models.DateTimeField()
    valid_to = models.DateTimeField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=['exploitation','parameter','scope','valid_from'])]
        constraints = [
            models.CheckConstraint(name='aquatic_rule_bounds',condition=(
                (models.Q(lower__isnull=False)|models.Q(upper__isnull=False)) &
                (models.Q(lower__isnull=True)|models.Q(upper__isnull=True)|models.Q(lower__lte=models.F('upper'))))),
            models.CheckConstraint(name='aquatic_rule_interval',condition=(
                models.Q(valid_to__isnull=True)|models.Q(valid_to__gt=models.F('valid_from')))),
            models.CheckConstraint(name='aquatic_rule_scope',condition=(
                models.Q(scope='INSTALLATION',installation__isnull=False,species__isnull=True,stage__isnull=True,system_code='') |
                models.Q(scope='SPECIES_STAGE',installation__isnull=True,species__isnull=False,stage__isnull=False,system_code='') |
                (models.Q(scope='SYSTEM',installation__isnull=True,species__isnull=True,stage__isnull=True) & ~models.Q(system_code=''))))
        ]


class StockFact(ImmutableFact):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    exploitation = models.ForeignKey('core.Exploitation', on_delete=models.PROTECT)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    operation_uuid = models.UUIDField()
    installation = models.ForeignKey(Installation, on_delete=models.PROTECT)
    cohort = models.ForeignKey(Cohort, null=True, blank=True, on_delete=models.PROTECT)
    parent = models.ForeignKey('self', null=True, blank=True, on_delete=models.PROTECT, related_name='children')
    reverses = models.OneToOneField('self', null=True, blank=True, on_delete=models.PROTECT, related_name='compensation')
    kind = models.CharField(max_length=20, choices=[(x,x) for x in
        ('MORTALITY','SUSPICION','RECONCILE','INVENTORY','ASSIGN_CREDIT','CANCEL')])
    quantity = models.PositiveBigIntegerField()
    occurred_at = models.DateTimeField()
    recorded_at = models.DateTimeField(auto_now_add=True)
    reason = models.CharField(max_length=500)
    note = models.CharField(max_length=1000, blank=True)
    payload = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [models.Index(fields=['exploitation','occurred_at','recorded_at','id'])]
        constraints = [
            models.CheckConstraint(name='aquatic_stock_fact_kind',
                condition=models.Q(kind__in=['MORTALITY','SUSPICION','RECONCILE','INVENTORY','ASSIGN_CREDIT','CANCEL'])),
            models.CheckConstraint(name='aquatic_stock_fact_quantity',
                condition=models.Q(quantity__gt=0)|models.Q(kind='INVENTORY',quantity=0)),
        ]
