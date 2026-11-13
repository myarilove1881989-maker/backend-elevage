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


class FeedPurchase(ImmutableFact):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    exploitation = models.ForeignKey('core.Exploitation', on_delete=models.PROTECT)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    command_uuid = models.UUIDField()
    receipt = models.OneToOneField('core.SupplyReceipt', on_delete=models.PROTECT)
    occurred_at = models.DateTimeField()
    recorded_at = models.DateTimeField(auto_now_add=True)

    def clean(self):
        if self.receipt.operation.exploitation_id!=self.exploitation_id or self.receipt.product.kind!='FEED':
            raise ValidationError('AQUATIC_REFERENCE_OUTSIDE_FARM')
        if not self.receipt.expense_id or self.receipt.expense.shared_exploitation_id!=self.exploitation_id:
            raise ValidationError('SHARED_FEED_EXPENSE_REQUIRED')


class FeedDistribution(ImmutableFact):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    exploitation = models.ForeignKey('core.Exploitation', on_delete=models.PROTECT)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    command_uuid = models.UUIDField()
    supply_operation = models.OneToOneField('core.SupplyOperation', on_delete=models.PROTECT)
    installation = models.ForeignKey(Installation, on_delete=models.PROTECT)
    cohort = models.ForeignKey(Cohort, null=True, blank=True, on_delete=models.PROTECT)
    product = models.ForeignKey('core.SupplyProduct', on_delete=models.PROTECT)
    quantity_kg = models.DecimalField(max_digits=14,decimal_places=3)
    leftovers_kg = models.DecimalField(max_digits=14,decimal_places=3,null=True,blank=True)
    occurred_at = models.DateTimeField()
    recorded_at = models.DateTimeField(auto_now_add=True)
    note = models.CharField(max_length=500,blank=True)
    analysis = models.JSONField()

    class Meta:
        constraints = [
            models.CheckConstraint(name='aquatic_feed_quantity_positive',condition=models.Q(quantity_kg__gt=0)),
            models.CheckConstraint(name='aquatic_feed_leftovers_valid',condition=models.Q(leftovers_kg__isnull=True) |
                (models.Q(leftovers_kg__gte=0)&models.Q(leftovers_kg__lte=models.F('quantity_kg')))),
        ]
        indexes = [models.Index(fields=['exploitation','occurred_at','id'])]

    def clean(self):
        for related in (self.supply_operation,self.installation,self.product):
            if related.exploitation_id!=self.exploitation_id:raise ValidationError('AQUATIC_REFERENCE_OUTSIDE_FARM')
        if self.cohort_id and self.cohort.exploitation_id!=self.exploitation_id:raise ValidationError('AQUATIC_REFERENCE_OUTSIDE_FARM')
        if self.product.kind!='FEED':raise ValidationError('AQUATIC_FEED_REQUIRED')
        if self.supply_operation.kind!='AQUA_FEED_DISTRIB' or self.supply_operation.movements.count()!=1:
            raise ValidationError('AQUATIC_SINGLE_FEED_DEBIT_REQUIRED')
        if not self.supply_operation.movements.filter(product=self.product,species__isnull=True,lot__isnull=True,
                quantity=-self.quantity_kg,kind='DISTRIBUTION').exists():
            raise ValidationError('AQUATIC_SINGLE_FEED_DEBIT_REQUIRED')


class SanitaryProduct(TenantEntity):
    """Only products not representable by the existing VACCINE catalogue."""
    name = models.CharField(max_length=120)
    type = models.CharField(max_length=12,choices=[('TREATMENT','Traitement'),('OTHER','Autre')])
    unit = models.CharField(max_length=8,choices=[(v,v) for v in ('dose','mL','L','g','kg','unit')])
    information = models.CharField(max_length=1000,blank=True)
    archived = models.BooleanField(default=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)

    class Meta:
        constraints=[models.CheckConstraint(name='aquatic_sanitary_product_type',condition=models.Q(type__in=['TREATMENT','OTHER'])),
            models.CheckConstraint(name='aquatic_sanitary_product_unit',condition=models.Q(unit__in=['dose','mL','L','g','kg','unit'])),
            models.UniqueConstraint(fields=['exploitation','name'],name='aquatic_sanitary_product_name')]

    def clean(self):
        super().clean()
        if self.created_by.exploitation_id!=self.exploitation_id:raise ValidationError('AQUATIC_REFERENCE_OUTSIDE_FARM')


class HealthRecord(ImmutableFact):
    id = models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    exploitation = models.ForeignKey('core.Exploitation',on_delete=models.PROTECT)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    command_uuid = models.UUIDField()
    kind = models.CharField(max_length=12,choices=[('OBSERVATION','Observation'),('TREATMENT','Traitement administré')])
    installation = models.ForeignKey(Installation,on_delete=models.PROTECT)
    cohort = models.ForeignKey(Cohort,null=True,blank=True,on_delete=models.PROTECT)
    occurred_at = models.DateTimeField()
    recorded_at = models.DateTimeField(auto_now_add=True)
    time_precision = models.CharField(max_length=8,default='DATETIME')
    business_date = models.DateField(null=True,blank=True)
    symptoms = models.CharField(max_length=2000,blank=True)
    behavior = models.CharField(max_length=2000,blank=True)
    note = models.CharField(max_length=2000,blank=True)
    supply_product = models.ForeignKey('core.SupplyProduct',null=True,blank=True,on_delete=models.PROTECT)
    sanitary_product = models.ForeignKey(SanitaryProduct,null=True,blank=True,on_delete=models.PROTECT)
    product_snapshot = models.JSONField(default=dict,blank=True)
    quantity = models.DecimalField(max_digits=18,decimal_places=6,null=True,blank=True)
    unit = models.CharField(max_length=8,blank=True)
    reason = models.CharField(max_length=1000,blank=True)
    observation = models.ForeignKey('self',null=True,blank=True,on_delete=models.PROTECT,related_name='treatment_links')
    ongoing = models.BooleanField(default=False)
    withdrawal_days = models.PositiveIntegerField(null=True,blank=True)
    withdrawal_source = models.CharField(max_length=1000,blank=True)
    withdrawal_until = models.DateTimeField(null=True,blank=True)
    eligible_cohorts = models.JSONField()
    species_ids = models.JSONField()
    scope_snapshot = models.JSONField()
    root_id = models.UUIDField()
    supersedes = models.OneToOneField('self',null=True,blank=True,on_delete=models.PROTECT,related_name='successor')
    correction_reason = models.CharField(max_length=1000,blank=True)

    class Meta:
        indexes=[models.Index(fields=['exploitation','occurred_at','id'],name='aquatic_health_history_idx')]
        constraints=[
            models.CheckConstraint(name='aquatic_health_payload',condition=(
                (models.Q(kind='OBSERVATION',quantity__isnull=True,supply_product__isnull=True,sanitary_product__isnull=True,unit='',reason='',observation__isnull=True,ongoing=False,withdrawal_days__isnull=True,withdrawal_until__isnull=True,withdrawal_source='') & (~models.Q(symptoms='')|~models.Q(behavior=''))) |
                (models.Q(kind='TREATMENT',symptoms='',behavior='',quantity__gt=0,quantity__isnull=False) & ~models.Q(reason='') & models.Q(unit__in=['dose','mL','L','g','kg','unit']) & (
                    models.Q(supply_product__isnull=False,sanitary_product__isnull=True)|models.Q(supply_product__isnull=True,sanitary_product__isnull=False))))),
            models.CheckConstraint(name='aquatic_health_withdrawal',condition=(
                models.Q(withdrawal_days__isnull=True,withdrawal_until__isnull=True) |
                (models.Q(kind='TREATMENT',withdrawal_days__isnull=False,withdrawal_until__isnull=False)&~models.Q(withdrawal_source='')))),
            models.CheckConstraint(name='aquatic_health_withdrawal_date',condition=models.Q(withdrawal_until__isnull=True)|models.Q(withdrawal_until__gte=models.F('occurred_at'))),
            models.CheckConstraint(name='aquatic_health_date_precision',condition=(models.Q(time_precision='DATETIME',business_date__isnull=True)|models.Q(time_precision='DATE',business_date__isnull=False))),
            models.CheckConstraint(name='aquatic_health_correction',condition=(models.Q(supersedes__isnull=True,correction_reason='')|(models.Q(supersedes__isnull=False)&~models.Q(correction_reason='')))),
        ]

    def clean(self):
        super().clean()
        if self.actor.exploitation_id!=self.exploitation_id:raise ValidationError('AQUATIC_REFERENCE_OUTSIDE_FARM')
        for related in (self.installation,self.cohort,self.supply_product,self.sanitary_product,self.observation,self.supersedes):
            if related and related.exploitation_id!=self.exploitation_id:raise ValidationError('AQUATIC_REFERENCE_OUTSIDE_FARM')
        if self.supersedes_id and (self.supersedes.kind!=self.kind or self.supersedes.root_id!=self.root_id):raise ValidationError('HEALTH_CORRECTION_KIND_MISMATCH')
        if self.supply_product_id and self.supply_product.kind!='VACCINE':raise ValidationError('SANITARY_PRODUCT_REQUIRED')


class BroodGroup(TenantEntity):
    name = models.CharField(max_length=120)
    species = models.ForeignKey('core.Espece',on_delete=models.PROTECT)
    installation = models.ForeignKey(Installation,on_delete=models.PROTECT)
    cohort = models.ForeignKey(Cohort,null=True,blank=True,on_delete=models.PROTECT)
    occurred_at = models.DateTimeField()
    time_precision = models.CharField(max_length=8,default='DATETIME')
    business_date = models.DateField(null=True,blank=True)
    males = models.PositiveIntegerField(null=True,blank=True)
    females = models.PositiveIntegerField(null=True,blank=True)
    note = models.CharField(max_length=2000,blank=True)
    status = models.CharField(max_length=8,default='ACTIVE')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    recorded_at = models.DateTimeField(auto_now_add=True)
    source_snapshot = models.JSONField()

    class Meta:
        constraints=[models.CheckConstraint(name='aquatic_brood_status',condition=models.Q(status__in=['ACTIVE','INACTIVE']))]


class Reproduction(TenantEntity):
    group = models.ForeignKey(BroodGroup,on_delete=models.PROTECT)
    species = models.ForeignKey('core.Espece',on_delete=models.PROTECT)
    installation = models.ForeignKey(Installation,on_delete=models.PROTECT)
    occurred_at = models.DateTimeField()
    time_precision = models.CharField(max_length=8,default='DATETIME')
    business_date = models.DateField(null=True,blank=True)
    note = models.CharField(max_length=2000,blank=True)
    technique = models.CharField(max_length=200,blank=True)
    status = models.CharField(max_length=8,default='OPEN')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    recorded_at = models.DateTimeField(auto_now_add=True)
    group_snapshot = models.JSONField()

    class Meta:
        constraints=[models.CheckConstraint(name='aquatic_reproduction_status',condition=models.Q(status__in=['OPEN','CLOSED']))]


class BreedingFact(ImmutableFact):
    id = models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    exploitation = models.ForeignKey('core.Exploitation',on_delete=models.PROTECT)
    reproduction = models.ForeignKey(Reproduction,on_delete=models.PROTECT)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    command_uuid = models.UUIDField()
    kind = models.CharField(max_length=12)
    installation = models.ForeignKey(Installation,on_delete=models.PROTECT)
    occurred_at = models.DateTimeField()
    recorded_at = models.DateTimeField(auto_now_add=True)
    time_precision = models.CharField(max_length=8,default='DATETIME')
    business_date = models.DateField(null=True,blank=True)
    quantity = models.PositiveIntegerField(null=True,blank=True)
    quality = models.CharField(max_length=10,default='UNKNOWN')
    parent = models.ForeignKey('self',null=True,blank=True,on_delete=models.PROTECT,related_name='children')
    cohort = models.OneToOneField(Cohort,null=True,blank=True,on_delete=models.PROTECT,related_name='fry_placement')
    stock_event = models.OneToOneField(Event,null=True,blank=True,on_delete=models.PROTECT)
    reason = models.CharField(max_length=1000,blank=True)
    note = models.CharField(max_length=2000,blank=True)
    payload = models.JSONField(default=dict,blank=True)

    class Meta:
        constraints=[
            models.CheckConstraint(name='aquatic_breeding_kind',condition=models.Q(kind__in=['SPAWN','INCUBATION','HATCH','COUNT','PLACEMENT','VOID'])),
            models.CheckConstraint(name='aquatic_breeding_quantity',condition=(models.Q(quality='UNKNOWN',quantity__isnull=True)|models.Q(quality__in=['COUNTED','ESTIMATED'],quantity__isnull=False))),
            models.CheckConstraint(name='aquatic_breeding_links',condition=(
                models.Q(kind__in=['SPAWN','INCUBATION','HATCH'],cohort__isnull=True,stock_event__isnull=True) |
                models.Q(kind='COUNT',quality='COUNTED',parent__isnull=False,cohort__isnull=True,stock_event__isnull=True) |
                models.Q(kind='PLACEMENT',quality='COUNTED',quantity__gt=0,parent__isnull=False,cohort__isnull=False,stock_event__isnull=False) |
                models.Q(kind='VOID',quality='UNKNOWN',quantity__isnull=True,parent__isnull=False,cohort__isnull=True))),
            models.UniqueConstraint(fields=['parent'],condition=models.Q(kind='VOID'),name='aquatic_breeding_void_once'),
        ]
        indexes=[models.Index(fields=['exploitation','occurred_at','id'],name='aquatic_breeding_history_idx')]

    def clean(self):
        super().clean()
        for related in (self.reproduction,self.actor,self.installation,self.parent,self.cohort,self.stock_event):
            if related and related.exploitation_id!=self.exploitation_id:raise ValidationError('AQUATIC_REFERENCE_OUTSIDE_FARM')
        if self.parent_id and self.parent.reproduction_id!=self.reproduction_id:raise ValidationError('BREEDING_PARENT_OUTSIDE_REPRODUCTION')
