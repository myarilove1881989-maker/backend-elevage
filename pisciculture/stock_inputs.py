from rest_framework import serializers as s
from .serializers import Envelope, Strict, Integer, At

class StockInput(Envelope):
    occurred_at = At()
    reason = s.CharField(max_length=500)
    note = s.CharField(max_length=1000,allow_blank=True,required=False,default='')
    expected_revision = Integer(min_value=0,required=False)

class MortalityInput(StockInput):
    installation = s.UUIDField()
    cohort = s.UUIDField(required=False)
    quantity = Integer(min_value=1,max_value=2147483647)
    suspected = s.BooleanField(required=False,default=False)

class Allocation(Strict):
    cohort = s.UUIDField()
    quantity = Integer(min_value=1,max_value=2147483647)

class ReconcileInput(StockInput):
    pending = s.UUIDField()
    allocations = Allocation(many=True,allow_empty=False)
    def validate_allocations(self,items):
        if len(items)>100 or len({x['cohort'] for x in items})!=len(items):
            raise s.ValidationError('DUPLICATE_OR_TOO_MANY_ALLOCATIONS')
        return items

class InventoryInput(StockInput):
    installation = s.UUIDField()
    observed_count = Integer(min_value=0,max_value=2147483647)
    expected_revision = Integer(min_value=0)

class AssignCreditInput(StockInput):
    credit = s.UUIDField()
    cohort = s.UUIDField()
    quantity = Integer(min_value=1,max_value=2147483647)

class CancelInput(StockInput):
    target = s.UUIDField()
