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
