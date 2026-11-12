from decimal import Decimal
from rest_framework import serializers as s
from .serializers import Envelope, Integer, DecimalString, At

PARAMETERS = {'temperature_c':'°C','ph':'pH','oxygen_mg_l':'mg/L','weight_age_days':'jours'}

class WeightInput(Envelope):
    cohort = s.UUIDField(required=False)
    installation = s.UUIDField(required=False)
    occurred_at = At()
    count_reference_at = At()
    sample_count = Integer(min_value=1,max_value=2147483647,required=False)
    mass_kg = DecimalString(max_digits=18,decimal_places=6,min_value=Decimal('0.000001'),required=False)
    direct_mean_g = DecimalString(max_digits=18,decimal_places=6,min_value=Decimal('0.000001'),required=False)
    note = s.CharField(max_length=500,allow_blank=True,required=False,default='')
    def validate(self,d):
        if ('cohort' in d)==('installation' in d):raise s.ValidationError('EXACTLY_ONE_TARGET')
        if d['count_reference_at']>d['occurred_at']:raise s.ValidationError('COUNT_REFERENCE_AFTER_WEIGHT')
        direct='direct_mean_g' in d
        if direct and ('sample_count' in d or 'mass_kg' in d):raise s.ValidationError('CHOOSE_WEIGHT_METHOD')
        if not direct and not {'sample_count','mass_kg'}<=d.keys():raise s.ValidationError('SAMPLE_PAIR_REQUIRED')
        return d

class WaterInput(Envelope):
    installation = s.UUIDField()
    occurred_at = At()
    temperature_c = DecimalString(max_digits=12,decimal_places=4,allow_null=True,required=False)
    ph = DecimalString(max_digits=12,decimal_places=4,allow_null=True,required=False)
    oxygen_mg_l = DecimalString(max_digits=12,decimal_places=4,allow_null=True,required=False)
    note = s.CharField(max_length=500,allow_blank=True,required=False,default='')
    def validate(self,d):
        if all(d.get(p) is None for p in ('temperature_c','ph','oxygen_mg_l')):
            raise s.ValidationError('AT_LEAST_ONE_READING')
        return d

class RuleAt(s.DateTimeField):
    def to_internal_value(self,data):
        if not isinstance(data,str) or not (data.endswith('Z') or '+' in data[10:] or '-' in data[10:]):
            raise s.ValidationError('TIMEZONE_REQUIRED')
        return super().to_internal_value(data)

class RuleInput(Envelope):
    supersedes = s.UUIDField(required=False)
    parameter = s.ChoiceField(choices=list(PARAMETERS))
    scope = s.ChoiceField(choices=['INSTALLATION','SPECIES_STAGE','SYSTEM'])
    installation = s.UUIDField(required=False)
    species = Integer(min_value=1,required=False)
    stage = s.UUIDField(required=False)
    system_code = s.CharField(max_length=80,required=False)
    lower = DecimalString(max_digits=18,decimal_places=4,required=False,allow_null=True)
    upper = DecimalString(max_digits=18,decimal_places=4,required=False,allow_null=True)
    valid_from = RuleAt()
    valid_to = RuleAt(required=False,allow_null=True)
    def validate(self,d):
        targets={k for k in ('installation','species','stage','system_code') if k in d}
        expected={'INSTALLATION':{'installation'},'SPECIES_STAGE':{'species','stage'},'SYSTEM':{'system_code'}}[d['scope']]
        if targets!=expected:raise s.ValidationError('RULE_TARGET_MISMATCH')
        lo,hi=d.get('lower'),d.get('upper')
        if lo is None and hi is None:raise s.ValidationError('RULE_BOUND_REQUIRED')
        if lo is not None and hi is not None and lo>hi:raise s.ValidationError('RULE_BOUNDS_REVERSED')
        if d.get('valid_to') and d['valid_to']<=d['valid_from']:raise s.ValidationError('RULE_INTERVAL_REVERSED')
        if d['parameter']=='weight_age_days' and (lo is not None or hi is None or hi<=0):
            raise s.ValidationError('FRESHNESS_POSITIVE_UPPER_REQUIRED')
        return d
