from decimal import Decimal
from rest_framework import serializers as s
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from .serializers import Envelope,Integer,DecimalString
from .health_inputs import HealthStrict

class Base(HealthStrict,Envelope):pass
class Scope(Base):
    scope=s.ChoiceField(choices=['COHORT','INSTALLATION','AQUATIC','FARM'])
    cohort=s.UUIDField(required=False,allow_null=True,default=None)
    installation=s.UUIDField(required=False,allow_null=True,default=None)
    reason=s.CharField(max_length=1000)
    def validate(self,d):
        if d['scope']=='COHORT' and not d['cohort']:raise s.ValidationError('COHORT_REQUIRED')
        if d['scope']=='INSTALLATION' and (not d['installation'] or d['cohort']):raise s.ValidationError('INSTALLATION_SCOPE_REQUIRED')
        if d['scope']in('AQUATIC','FARM') and (d['cohort'] or d['installation']):raise s.ValidationError('COMMON_SCOPE_HAS_NO_TARGET')
        return d
class ExpenseInput(Scope):
    category=Integer(min_value=1,required=False)
    new_category=s.CharField(max_length=50,required=False)
    amount=DecimalString(max_digits=12,decimal_places=2,min_value=Decimal('0.01'))
    date=s.DateField()
    note=s.CharField(max_length=255,allow_blank=True,required=False,default='')
    def validate(self,d):
        super().validate(d)
        if ('category'in d)==('new_category'in d):raise s.ValidationError('ONE_CATEGORY_REQUIRED')
        if d['date']>timezone.localdate():raise s.ValidationError('FUTURE_EXPENSE_DATE')
        return d
class ExpenseEdit(ExpenseInput):
    target=s.UUIDField()
    expected_version=Integer(min_value=1)
class AssignInput(Scope):
    expense=Integer(min_value=1)
    expected_version=Integer(min_value=0)
class AwareFuture(s.DateTimeField):
    def to_internal_value(self,d):
        if not isinstance(d,str) or not (d.endswith('Z') or '+'in d[10:] or '-'in d[10:]):raise s.ValidationError('TIMEZONE_REQUIRED')
        return super().to_internal_value(d)
class TaskInput(Base):
    title=s.CharField(max_length=255)
    kind=s.ChoiceField(choices=['WATER','WEIGHT','FEED','OBSERVATION','TREATMENT','REPRODUCTION','HATCH_CHECK','HARVEST','OTHER'])
    date=s.DateField()
    scheduled_at=AwareFuture(required=False,allow_null=True,default=None)
    reminder_at=AwareFuture(required=False,allow_null=True,default=None)
    installation=s.UUIDField(required=False,allow_null=True,default=None)
    cohort=s.UUIDField(required=False,allow_null=True,default=None)
    description=s.CharField(max_length=10000,allow_blank=True,required=False,default='')
    def validate(self,d):
        raw=self.initial_data.get('scheduled_at')
        if raw and parse_datetime(raw).date()!=d['date']:raise s.ValidationError('TASK_DATE_TIME_MISMATCH')
        if d['reminder_at'] and d['scheduled_at'] and d['reminder_at']>d['scheduled_at']:raise s.ValidationError('REMINDER_AFTER_SCHEDULE')
        return d
class TaskEdit(TaskInput):
    target=s.UUIDField()
    expected_version=Integer(min_value=1)
    reason=s.CharField(max_length=1000)
class Progress(Base):
    target=s.UUIDField()
    expected_version=Integer(min_value=1)
    status=s.ChoiceField(choices=['TODO','DONE','CANCELLED'])
    reason=s.CharField(max_length=1000)
    report=s.CharField(max_length=10000,allow_blank=True,required=False,default='')
    actual_kind=s.ChoiceField(choices=['','WATER','WEIGHT','FEED','OBSERVATION','TREATMENT','REPRODUCTION','HATCH','HARVEST'],required=False,default='')
    actual_id=s.UUIDField(required=False,allow_null=True,default=None)
    def validate(self,d):
        if bool(d['actual_kind'])!=bool(d['actual_id']):raise s.ValidationError('ACTUAL_REFERENCE_PAIR_REQUIRED')
        if d['actual_id'] and d['status']!='DONE':raise s.ValidationError('ACTUAL_LINK_REQUIRES_DONE')
        return d
