"""Online terrestrial observations; never extends signed Android declarations."""
import uuid
from django.db import models
from django.conf import settings
from django.core.exceptions import ValidationError
from core.audit import AuditedModel

class TenantEntity(AuditedModel):
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    exploitation=models.ForeignKey('core.Exploitation',on_delete=models.PROTECT,related_name='%(app_label)s_%(class)s_rows')
    version=models.PositiveIntegerField(default=1)
    class Meta: abstract=True
    def clean(self):
        for field in ('species','lot','parameter','identity','cycle','movement'):
            if getattr(self,field+'_id',None):
                obj=getattr(self,field)
                if obj.exploitation_id!=self.exploitation_id:raise ValidationError('REFERENCE_OUTSIDE_FARM')
    def save(self,*args,**kwargs):
        self.clean();return super().save(*args,**kwargs)

class ImmutableQuerySet(models.QuerySet):
    def update(self,**kwargs):raise ValidationError('HISTORY_IMMUTABLE')
    def delete(self):raise ValidationError('HISTORY_IMMUTABLE')
    def bulk_update(self,*args,**kwargs):raise ValidationError('HISTORY_IMMUTABLE')

class Fact(TenantEntity):
    objects=ImmutableQuerySet.as_manager()
    actor=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,related_name='%(app_label)s_%(class)s_rows')
    recorded_at=models.DateTimeField(auto_now_add=True)
    class Meta: abstract=True
    def save(self,*args,**kwargs):
        if not self._state.adding:raise ValidationError('HISTORY_IMMUTABLE')
        return super().save(*args,**kwargs)
    def delete(self,*args,**kwargs):raise ValidationError('HISTORY_IMMUTABLE')

class Command(Fact):
    operation_uuid=models.UUIDField()
    kind=models.CharField(max_length=40)
    fingerprint=models.CharField(max_length=64)
    response=models.JSONField()
    class Meta:
        constraints=[models.UniqueConstraint(fields=['exploitation','operation_uuid'],name='husbandry_unique_command')]

class TechnicalParameter(TenantEntity):
    species=models.ForeignKey('core.Espece',on_delete=models.PROTECT)
    code=models.CharField(max_length=80)
    name=models.CharField(max_length=120)
    kind=models.CharField(max_length=8,choices=[('NUMBER','Number'),('TEXT','Text'),('BOOL','Boolean')])
    unit=models.CharField(max_length=30,blank=True)
    predefined=models.BooleanField(default=False)
    class Meta:
        constraints=[models.UniqueConstraint(fields=['exploitation','species','code'],name='husbandry_parameter_code'),models.CheckConstraint(condition=models.Q(kind__in=['NUMBER','TEXT','BOOL']),name='husbandry_parameter_type')]

class ParameterRule(Fact):
    lot=models.ForeignKey('core.Lot',on_delete=models.PROTECT)
    parameter=models.ForeignKey(TechnicalParameter,on_delete=models.PROTECT)
    valid_from=models.DateTimeField()
    visible=models.BooleanField(default=True)
    alerts_enabled=models.BooleanField(default=False)
    lower=models.DecimalField(max_digits=20,decimal_places=6,null=True,blank=True)
    upper=models.DecimalField(max_digits=20,decimal_places=6,null=True,blank=True)
    supersedes=models.OneToOneField('self',on_delete=models.PROTECT,null=True,blank=True,related_name='successor')
    class Meta:
        indexes=[models.Index(fields=['exploitation','lot','parameter','valid_from'])]
        constraints=[models.CheckConstraint(condition=models.Q(lower__isnull=True)|models.Q(upper__isnull=True)|models.Q(lower__lte=models.F('upper')),name='husbandry_rule_bounds')]
    def clean(self):
        super().clean()
        if self.lot.espece_id!=self.parameter.species_id:raise ValidationError('PARAMETER_SPECIES_MISMATCH')
        if self.parameter.kind!='NUMBER' and (self.lower is not None or self.upper is not None or self.alerts_enabled):raise ValidationError('NUMERIC_RULE_REQUIRED')

class TechnicalReading(Fact):
    lot=models.ForeignKey('core.Lot',on_delete=models.PROTECT)
    occurred_at=models.DateTimeField()
    note=models.CharField(max_length=2000,blank=True)
    values=models.JSONField()
    class Meta:indexes=[models.Index(fields=['exploitation','lot','occurred_at','id'])]

class BreederIdentity(TenantEntity):
    species=models.ForeignKey('core.Espece',on_delete=models.PROTECT)
    label=models.CharField(max_length=100)
    normalized_label=models.CharField(max_length=100)
    class Meta:constraints=[models.UniqueConstraint(fields=['exploitation','species','normalized_label'],name='husbandry_identity_label')]

class ReproductionCycle(TenantEntity):
    lot=models.ForeignKey('core.Lot',on_delete=models.PROTECT)
    identity=models.ForeignKey(BreederIdentity,on_delete=models.PROTECT,null=True,blank=True)
    mode=models.CharField(max_length=12,choices=[('INDIVIDUAL','Individual'),('COLLECTIVE','Collective')])
    name=models.CharField(max_length=120)
    female_count=models.PositiveIntegerField(default=1)
    started_on=models.DateField()
    expected_on=models.DateField(null=True,blank=True)
    status=models.CharField(max_length=8,default='OPEN')
    class Meta:
        indexes=[models.Index(fields=['exploitation','lot','started_on'])]
        constraints=[models.CheckConstraint(condition=models.Q(status__in=['OPEN','CLOSED']),name='husbandry_cycle_status'),models.CheckConstraint(condition=(models.Q(mode='INDIVIDUAL',identity__isnull=False,female_count=1)|models.Q(mode='COLLECTIVE',identity__isnull=True,female_count__gt=0)),name='husbandry_cycle_mode')]
    def clean(self):
        super().clean()
        if self.identity_id and self.identity.species_id!=self.lot.espece_id:raise ValidationError('IDENTITY_SPECIES_MISMATCH')

class ReproductionEvent(Fact):
    cycle=models.ForeignKey(ReproductionCycle,on_delete=models.PROTECT,related_name='events')
    kind=models.CharField(max_length=12)
    occurred_on=models.DateField()
    data=models.JSONField(default=dict)
    note=models.CharField(max_length=2000,blank=True)
    movement=models.OneToOneField('core.Mouvement',on_delete=models.PROTECT,null=True,blank=True)
    class Meta:
        indexes=[models.Index(fields=['exploitation','cycle','occurred_on','id'])]
        constraints=[models.CheckConstraint(condition=models.Q(kind__in=['MATING','GESTATION','NEST','BIRTH','WEAN','CLOSE']),name='husbandry_event_kind')]
