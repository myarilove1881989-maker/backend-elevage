from decimal import Decimal
from rest_framework import serializers as s
from .serializers import Envelope,Integer,At,DecimalString
from .health_inputs import HealthStrict
from .breeding_inputs import ExplicitCount

class Base(HealthStrict,Envelope):
    occurred_at=At()
    note=s.CharField(max_length=2000,allow_blank=True,required=False,default='')
class Review(HealthStrict,s.Serializer):
    health_token=s.CharField(max_length=4000)
    human_verified=ExplicitCount()
    verification_reason=s.CharField(max_length=1000)
class Customer(HealthStrict,s.Serializer):
    nom=s.CharField(max_length=255)
    telephone=s.CharField(max_length=20,allow_blank=True,required=False,default='')
    pays=s.CharField(max_length=2,allow_blank=True,required=False,default='')
    ville=s.CharField(max_length=100,allow_blank=True,required=False,default='')
class Price(HealthStrict,s.Serializer):
    client=Integer(min_value=1)
    unit_price=DecimalString(max_digits=10,decimal_places=2,min_value=Decimal('0.00'))
class HarvestInput(Base):
    cohort=s.UUIDField()
    installation=s.UUIDField()
    live_count=Integer(min_value=1,max_value=2147483647)
    counted=ExplicitCount()
    mass_kg=DecimalString(max_digits=18,decimal_places=6,min_value=Decimal('0.000001'),allow_null=True,required=False,default=None)
    mass_quality=s.ChoiceField(choices=['UNKNOWN','MEASURED','ESTIMATED'])
    stock_unit=s.ChoiceField(choices=['KG','PIECE'])
    destination=s.ChoiceField(choices=['DIRECT','STOCK'])
    storage_place=s.CharField(max_length=200,required=False,default='',allow_blank=True)
    storage_mode=s.CharField(max_length=120,required=False,default='',allow_blank=True)
    conservation_note=s.CharField(max_length=500,required=False,default='',allow_blank=True)
    review=Review()
    direct_sale=Price(required=False)
    def validate(self,d):
        if (d['mass_quality']=='UNKNOWN')!=(d['mass_kg'] is None):raise s.ValidationError('HARVEST_MEASURE_QUALITY_MISMATCH')
        if d['stock_unit']=='KG' and d['mass_quality']!='MEASURED':raise s.ValidationError('MEASURED_KG_REQUIRED')
        if (d['destination']=='DIRECT')!=('direct_sale' in d):raise s.ValidationError('DIRECT_SALE_PAIR_REQUIRED')
        if d['destination']=='STOCK' and (not d['storage_place'] or not d['storage_mode']):raise s.ValidationError('STORAGE_DETAILS_REQUIRED')
        return d
class MoveInput(Base):
    harvest=s.UUIDField()
    quantity=DecimalString(max_digits=18,decimal_places=6,min_value=Decimal('0.000001'))
    count=Integer(min_value=0,max_value=2147483647,allow_null=True,required=False,default=None)
    mass_kg=DecimalString(max_digits=18,decimal_places=6,min_value=Decimal('0.000001'),allow_null=True,required=False,default=None)
class SaleInput(MoveInput):
    price=Price()
    review=Review()
class LossInput(MoveInput):
    reason=s.CharField(max_length=1000)
class PaymentInput(Base):
    sale=Integer(min_value=1)
    amount=DecimalString(max_digits=12,decimal_places=2,min_value=Decimal('0.01'))
class ClientInput(Base):
    customer=Customer()
class VoidInput(Base):
    harvest=s.UUIDField()
    target=s.UUIDField(required=False,allow_null=True,default=None)
    reason=s.CharField(max_length=1000)
class CorrectionInput(VoidInput):pass
