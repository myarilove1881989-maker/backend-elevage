from decimal import Decimal
from rest_framework import serializers as s
from .serializers import Envelope,Strict,Integer,DecimalString,At

class NewProduct(Strict):
    id=s.UUIDField()
    name=s.CharField(max_length=120)

class FeedMoment(Envelope):
    time_precision=s.ChoiceField(choices=['DATETIME','DATE'],required=False,default='DATETIME')
    business_date=s.DateField(required=False)
    def validate_moment(self,d):
        if d['time_precision']=='DATE':
            from django.utils.dateparse import parse_datetime
            raw=parse_datetime(self.initial_data.get('occurred_at',''))
            if not d.get('business_date') or raw is None or raw.date()!=d['business_date'] or raw.time().replace(tzinfo=None).isoformat()!='00:00:00':
                raise s.ValidationError('DATE_PRECISION_REQUIRES_DATE_AND_MIDNIGHT')
        elif 'business_date' in d:raise s.ValidationError('BUSINESS_DATE_ONLY_FOR_DATE_PRECISION')
        return d

class PurchaseInput(FeedMoment):
    product=s.UUIDField(required=False)
    new_product=NewProduct(required=False)
    category=Integer(min_value=1,required=False)
    new_category=s.CharField(max_length=50,required=False)
    quantity_kg=DecimalString(max_digits=14,decimal_places=3,min_value=Decimal('0.001'))
    amount=DecimalString(max_digits=12,decimal_places=2,min_value=Decimal('0'))
    unit=s.ChoiceField(choices=['kg'])
    occurred_at=At()
    note=s.CharField(max_length=255,required=False,allow_blank=True,default='')
    def validate(self,d):
        if ('product'in d)==('new_product'in d):raise s.ValidationError('CHOOSE_ONE_FEED_PRODUCT')
        if ('category'in d)==('new_category'in d):raise s.ValidationError('CHOOSE_ONE_FEED_CATEGORY')
        return self.validate_moment(d)

class DistributionInput(FeedMoment):
    product=s.UUIDField()
    installation=s.UUIDField()
    cohort=s.UUIDField(required=False,allow_null=True,default=None)
    quantity_kg=DecimalString(max_digits=14,decimal_places=3,min_value=Decimal('0.001'))
    leftovers_kg=DecimalString(max_digits=14,decimal_places=3,min_value=Decimal('0'),required=False,allow_null=True,default=None)
    unit=s.ChoiceField(choices=['kg'])
    occurred_at=At()
    note=s.CharField(max_length=500,required=False,allow_blank=True,default='')
    weight_reference=s.UUIDField(required=False)
    def validate(self,d):
        if d['leftovers_kg'] is not None and d['leftovers_kg']>d['quantity_kg']:raise s.ValidationError('LEFTOVERS_EXCEED_DISTRIBUTION')
        return self.validate_moment(d)

class Pool(Strict):
    species=Integer(min_value=1,required=False,allow_null=True,default=None)
    lot=Integer(min_value=1,required=False,allow_null=True,default=None)
    def validate(self,d):
        if d['lot'] and not d['species']:raise s.ValidationError('RESERVE_SPECIES_REQUIRED')
        return d

class ReserveInput(Envelope):
    product=s.UUIDField()
    source=Pool()
    target=Pool()
    quantity_kg=DecimalString(max_digits=14,decimal_places=3,min_value=Decimal('0.001'))
    unit=s.ChoiceField(choices=['kg'])
    occurred_at=At()
    reason=s.CharField(max_length=500)
    def validate(self,d):
        if d['source']==d['target']:raise s.ValidationError('SAME_FEED_RESERVE')
        return d
