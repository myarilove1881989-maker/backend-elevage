from rest_framework import serializers as s
from django.utils import timezone
from decimal import Decimal


class Strict(s.Serializer):
    def to_internal_value(self, data):
        if not isinstance(data, dict) or set(data) - set(self.fields):
            raise s.ValidationError('UNKNOWN_FIELDS')
        return super().to_internal_value(data)


class Integer(s.IntegerField):
    def to_internal_value(self, data):
        if type(data) is not int:
            raise s.ValidationError('EXPLICIT_INTEGER_REQUIRED')
        return super().to_internal_value(data)


class At(s.DateTimeField):
    def to_internal_value(self, data):
        if not isinstance(data,str) or not (data.endswith('Z') or '+' in data[10:] or '-' in data[10:]):
            raise s.ValidationError('TIMEZONE_REQUIRED')
        value = super().to_internal_value(data)
        if value > timezone.now() + timezone.timedelta(minutes=5):
            raise s.ValidationError('FUTURE_DATE')
        return value


class DecimalString(s.DecimalField):
    def to_internal_value(self,data):
        if not isinstance(data,str):
            raise s.ValidationError('DECIMAL_STRING_REQUIRED')
        return super().to_internal_value(data)


class Envelope(Strict):
    operation_uuid = s.UUIDField()
    schema_version = Integer(min_value=1, max_value=1)


class Activation(Envelope):
    enabled = s.BooleanField()


class InstallationInput(Envelope):
    id = s.UUIDField(required=False)
    code = s.CharField(max_length=50, required=False)
    name = s.CharField(max_length=120, required=False)
    type = s.ChoiceField(choices=['BASSIN','ETANG','CAGE','BAC'], required=False)
    system_code = s.CharField(max_length=80, allow_blank=True, required=False)
    capacity = DecimalString(max_digits=18,decimal_places=3,min_value=Decimal('0.001'),allow_null=True,required=False)
    capacity_unit = s.ChoiceField(choices=['','m3','L','m2'],required=False)
    location = s.CharField(max_length=200,allow_blank=True,required=False)
    status = s.ChoiceField(choices=['ACTIVE','INACTIVE'],required=False)


class SpeciesInput(Envelope):
    id = s.UUIDField()
    species = Integer(min_value=1, required=False)
    name = s.CharField(max_length=100, required=False)


class StageInput(Envelope):
    id = s.UUIDField()
    species = Integer(min_value=1)
    code = s.CharField(max_length=50)
    name = s.CharField(max_length=100)


class Allocation(Strict):
    installation = s.UUIDField()
    count = Integer(min_value=1, max_value=2147483647)


class Origin(Strict):
    type = s.ChoiceField(choices=['PURCHASE','HATCH','TRANSFER_IN','OTHER'])
    reference = s.CharField(max_length=200, allow_null=True, allow_blank=True, required=False)


class CohortInput(Envelope):
    id = s.UUIDField()
    code = s.CharField(max_length=50)
    name = s.CharField(max_length=120)
    species = Integer(min_value=1)
    stage = s.UUIDField(allow_null=True, required=False)
    origin = Origin()
    started_at = At()
    initial_count = Integer(min_value=1,max_value=2147483647)
    allocations = Allocation(many=True, allow_empty=False)
    reason = s.CharField(max_length=500)


class CohortPatch(Envelope):
    name = s.CharField(max_length=120,required=False)
    stage = s.UUIDField(required=False,allow_null=True)
    status = s.ChoiceField(choices=['OPEN','CLOSED'],required=False)


class Versions(Strict):
    cohort = Integer(min_value=1)
    source_position = Integer(min_value=1)
    destination_position = Integer(min_value=0)


class Transfer(Envelope):
    cohort = s.UUIDField()
    source = s.UUIDField()
    destination = s.UUIDField()
    count = Integer(min_value=1,max_value=2147483647)
    occurred_at = At()
    reason = s.CharField(max_length=500)
    expected_versions = Versions()


class Onboarding(Envelope):
    installation = s.DictField()
    species = s.DictField()
    cohort = s.DictField()
