from decimal import Decimal
from django.utils.dateparse import parse_datetime
from rest_framework import serializers as s
from .serializers import Strict,Envelope,At,Integer,DecimalString

UNITS=['dose','mL','L','g','kg','unit']

class HealthStrict:
    def to_internal_value(self,data):
        if not isinstance(data,dict) or set(data)-set(self.fields):
            raise s.ValidationError({'non_field_errors':['UNKNOWN_HEALTH_FIELDS']})
        return super().to_internal_value(data)

class HealthMoment(HealthStrict,Strict):
    installation=s.UUIDField()
    cohort=s.UUIDField(required=False,allow_null=True,default=None)
    occurred_at=At()
    time_precision=s.ChoiceField(choices=['DATE','DATETIME'],required=False,default='DATETIME')
    business_date=s.DateField(required=False)
    note=s.CharField(max_length=2000,allow_blank=True,required=False,default='')
    def to_internal_value(self,data):
        self._raw_occurred_at=data.get('occurred_at','') if isinstance(data,dict) else ''
        return super().to_internal_value(data)

    def moment(self,d):
        if d['time_precision']=='DATE':
            raw=parse_datetime(self._raw_occurred_at)
            if raw is None or raw.date()!=d.get('business_date') or raw.time().replace(tzinfo=None).isoformat()!='00:00:00':
                raise s.ValidationError('DATE_PRECISION_REQUIRES_DATE_AND_MIDNIGHT')
        elif 'business_date' in d:raise s.ValidationError('BUSINESS_DATE_ONLY_FOR_DATE_PRECISION')
        return d

class ObservationFields(HealthMoment):
    symptoms=s.CharField(max_length=2000,allow_blank=True,required=False,default='')
    behavior=s.CharField(max_length=2000,allow_blank=True,required=False,default='')
    def validate(self,d):
        if not d['symptoms'] and not d['behavior']:raise s.ValidationError('SYMPTOMS_OR_BEHAVIOR_REQUIRED')
        return self.moment(d)

class ObservationInput(ObservationFields):
    operation_uuid=s.UUIDField()
    schema_version=Integer(min_value=1,max_value=1)

class TreatmentFields(HealthMoment):
    supply_product=s.UUIDField(required=False)
    sanitary_product=s.UUIDField(required=False)
    quantity=DecimalString(max_digits=18,decimal_places=6,min_value=Decimal('0.000001'))
    unit=s.ChoiceField(choices=UNITS)
    administration_status=s.ChoiceField(choices=['ADMINISTERED'])
    reason=s.CharField(max_length=1000)
    observation=s.UUIDField(required=False,allow_null=True,default=None)
    ongoing=s.BooleanField(required=False,default=False)
    withdrawal_days=Integer(min_value=0,max_value=36500,required=False,allow_null=True,default=None)
    withdrawal_source=s.CharField(max_length=1000,allow_blank=True,required=False,default='')
    def validate(self,d):
        if ('supply_product' in d)==('sanitary_product' in d):raise s.ValidationError('CHOOSE_ONE_SANITARY_PRODUCT')
        if d['withdrawal_days'] is not None and not d['withdrawal_source']:raise s.ValidationError('DOCUMENTED_WITHDRAWAL_SOURCE_REQUIRED')
        return self.moment(d)

class TreatmentInput(TreatmentFields):
    operation_uuid=s.UUIDField()
    schema_version=Integer(min_value=1,max_value=1)

class HealthCorrectionInput(HealthStrict,Envelope):
    target=s.UUIDField()
    reason=s.CharField(max_length=1000)
    observation=ObservationFields(required=False)
    treatment=TreatmentFields(required=False)
    def validate(self,d):
        if ('observation'in d)==('treatment'in d):raise s.ValidationError('ONE_COMPLETE_REPLACEMENT_REQUIRED')
        return d

class SanitaryProductInput(HealthStrict,Envelope):
    id=s.UUIDField()
    name=s.CharField(max_length=120)
    type=s.ChoiceField(choices=['TREATMENT','OTHER'])
    unit=s.ChoiceField(choices=UNITS)
    information=s.CharField(max_length=1000,allow_blank=True,required=False,default='')
