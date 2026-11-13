from rest_framework import serializers as s
from .serializers import Envelope,Integer
from .health_inputs import HealthMoment,HealthStrict

class ExplicitCount(s.BooleanField):
    def to_internal_value(self,data):
        if data is not True:raise s.ValidationError('EXPLICIT_TRUE_REQUIRED')
        return True

class BreedingMoment(HealthMoment):
    cohort=None

class GroupInput(HealthMoment):
    operation_uuid=s.UUIDField()
    schema_version=Integer(min_value=1,max_value=1)
    id=s.UUIDField()
    name=s.CharField(max_length=120)
    species=Integer(min_value=1)
    males=Integer(min_value=0,max_value=2147483647,required=False,allow_null=True,default=None)
    females=Integer(min_value=0,max_value=2147483647,required=False,allow_null=True,default=None)
    def validate(self,d):return self.moment(d)

class GroupPatch(HealthStrict,Envelope):
    target=s.UUIDField()
    expected_version=Integer(min_value=1)
    reason=s.CharField(max_length=1000)
    name=s.CharField(max_length=120,required=False)
    installation=s.UUIDField(required=False)
    males=Integer(min_value=0,max_value=2147483647,required=False,allow_null=True)
    females=Integer(min_value=0,max_value=2147483647,required=False,allow_null=True)
    note=s.CharField(max_length=2000,required=False,allow_blank=True)
    status=s.ChoiceField(choices=['ACTIVE','INACTIVE'],required=False)

class ReproductionInput(BreedingMoment):
    operation_uuid=s.UUIDField()
    schema_version=Integer(min_value=1,max_value=1)
    id=s.UUIDField()
    group=s.UUIDField()
    technique=s.CharField(max_length=200,allow_blank=True,required=False,default='')
    def validate(self,d):return self.moment(d)

class ReproductionPatch(HealthStrict,Envelope):
    target=s.UUIDField()
    expected_version=Integer(min_value=1)
    reason=s.CharField(max_length=1000)
    status=s.ChoiceField(choices=['OPEN','CLOSED'])

class StepInput(BreedingMoment):
    operation_uuid=s.UUIDField()
    schema_version=Integer(min_value=1,max_value=1)
    reproduction=s.UUIDField()
    kind=s.ChoiceField(choices=['SPAWN','INCUBATION','HATCH'])
    parent=s.UUIDField(required=False,allow_null=True,default=None)
    quality=s.ChoiceField(choices=['COUNTED','ESTIMATED','UNKNOWN'])
    quantity=Integer(min_value=0,max_value=2147483647,required=False,allow_null=True,default=None)
    def validate(self,d):
        if (d['quality']=='UNKNOWN')!=(d['quantity'] is None):raise s.ValidationError('BREEDING_QUANTITY_QUALITY_MISMATCH')
        return self.moment(d)

class CountInput(BreedingMoment):
    operation_uuid=s.UUIDField()
    schema_version=Integer(min_value=1,max_value=1)
    hatch=s.UUIDField()
    quantity=Integer(min_value=0,max_value=2147483647)
    actually_counted=ExplicitCount()
    reason=s.CharField(max_length=1000)
    def validate(self,d):
        if not d['actually_counted']:raise s.ValidationError('EXPLICIT_COUNT_CONFIRMATION_REQUIRED')
        return self.moment(d)

class PlacementInput(BreedingMoment):
    operation_uuid=s.UUIDField()
    schema_version=Integer(min_value=1,max_value=1)
    hatch=s.UUIDField()
    quantity=Integer(min_value=1,max_value=2147483647)
    actually_counted=ExplicitCount()
    id=s.UUIDField()
    name=s.CharField(max_length=120)
    code=s.CharField(max_length=50)
    stage=s.UUIDField(required=False,allow_null=True,default=None)
    confirm_total=Integer(min_value=0,max_value=2147483647,required=False)
    confirmation_reason=s.CharField(max_length=1000,required=False)
    reason=s.CharField(max_length=500)
    def validate(self,d):
        if not d['actually_counted']:raise s.ValidationError('EXPLICIT_STOCK_CONFIRMATION_REQUIRED')
        if ('confirm_total'in d)!=('confirmation_reason'in d):raise s.ValidationError('CONFIRMED_TOTAL_AND_REASON_REQUIRED')
        return self.moment(d)

class VoidInput(HealthStrict,Envelope):
    target=s.UUIDField()
    reason=s.CharField(max_length=1000)
    # Same strict, bounded, timezone-aware instant as other aquatic operations.
    from .serializers import At
    occurred_at=At()
