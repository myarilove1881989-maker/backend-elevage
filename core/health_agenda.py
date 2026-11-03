import hashlib,json
from datetime import datetime,time,timedelta
from uuid import UUID
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import ValidationError,PermissionDenied
from .models import Task,Exploitation,Lot,ExploitationMembership,AuditEvent
from .health_models import HealthTask,HealthCommand,VaccinationTaskLink
from .supply_models import SupplyProduct
from .access_policy import authorize,effective_permissions
from .lot_lifecycle import ensure_open
from .feed_purchases import checked


def authorized_assignee(farm_id,user_id):
    member=ExploitationMembership.objects.select_related('user','exploitation').filter(exploitation_id=farm_id,user_id=user_id,
        user__exploitation_id=farm_id,role='OPERATEUR',is_active=True,user__is_active=True).first()
    if member is None or not {'agenda.read','agenda.progress','vaccinations.create'}<=effective_permissions(member):
        raise ValidationError('HEALTH_ASSIGNEE_NOT_AUTHORIZED')
    return member


class PlanInput(serializers.Serializer):
    operation_uuid=serializers.UUIDField()
    product=serializers.UUIDField()
    lot=serializers.IntegerField(min_value=1)
    species=serializers.IntegerField(min_value=1)
    date=serializers.DateField()
    planned_time=serializers.TimeField(required=False,allow_null=True)
    assigned_to=serializers.IntegerField(min_value=1)
    interval_days=serializers.IntegerField(required=False,min_value=1,max_value=365,allow_null=True)
    end_date=serializers.DateField(required=False,allow_null=True)
    observations=serializers.CharField(required=False,allow_blank=True,max_length=10000)

    def to_internal_value(self,data):
        if not isinstance(data,dict) or set(data)-set(self.fields):raise ValidationError({'non_field_errors':['UNKNOWN_HEALTH_PLAN_FIELDS']})
        if any(isinstance(data.get(key),(bool,float)) for key in ('interval_days','lot','species','assigned_to')):raise ValidationError({'non_field_errors':['EXPLICIT_INTEGER_REQUIRED']})
        return super().to_internal_value(data)

    def validate(self,p):
        if p.get('planned_time') and p['planned_time'].tzinfo is not None:raise ValidationError('HEALTH_LOCAL_TIME_REQUIRED')
        if set(self.initial_data)-set(self.fields):raise ValidationError('UNKNOWN_HEALTH_PLAN_FIELDS')
        if p.get('interval_days'):
            if not p.get('end_date') or not 0<=(p['end_date']-p['date']).days<=365:raise ValidationError('EXPLICIT_RECURRENCE_END_REQUIRED')
        elif p.get('end_date'):raise ValidationError('INTERVAL_REQUIRED_FOR_RECURRENCE')
        return p


def command(actor,body,kind):
    try:
        identifier=UUID(str(body.get('operation_uuid')))
        encoded=json.dumps({'payload':body,'author':actor.pk,'kind':kind},sort_keys=True,separators=(',',':'),allow_nan=False)
    except (ValueError,TypeError):raise ValidationError('HEALTH_COMMAND_INVALID')
    if len(encoded)>20000:raise ValidationError('HEALTH_COMMAND_TOO_LARGE')
    digest=hashlib.sha256(encoded.encode()).hexdigest()
    prior=HealthCommand.objects.filter(exploitation_id=actor.exploitation_id,operation_uuid=identifier).first()
    if prior and prior.fingerprint!=digest:raise ValidationError('HEALTH_UUID_PAYLOAD_CONFLICT')
    return identifier,digest,prior


@transaction.atomic
def plan(request,body):
    actor=request.user
    farm=Exploitation.objects.select_for_update().get(pk=actor.exploitation_id)
    authorize(actor,farm,'agenda.plan');authorize(actor,farm,'agenda.read')
    identifier,digest,prior=command(actor,body,'PLAN')
    if prior:return list(Task.objects.filter(exploitation=farm,pk__in=prior.task_ids).order_by('pk'))
    incoming=PlanInput(data=body);incoming.is_valid(raise_exception=True);p=incoming.validated_data
    product=SupplyProduct.objects.filter(pk=p['product'],exploitation=farm,kind='VACCINE',archived=False).first()
    lot=Lot.objects.filter(pk=p['lot'],exploitation=farm,espece_id=p['species']).first()
    if product is None or lot is None:raise ValidationError('HEALTH_PLAN_OUTSIDE_FARM')
    ensure_open(lot);authorized_assignee(farm.pk,p['assigned_to'])
    # Bounded explicit occurrences, using existing Task records and serializer.
    from .task_views import AgendaSerializer
    dates=[p['date']]
    if p.get('interval_days'):
        dates=[p['date']+timedelta(days=i) for i in range(0,(p['end_date']-p['date']).days+1,p['interval_days'])]
    tasks=[]
    for day in dates:
        existing=HealthTask.objects.filter(product=product,lot=lot,planned_time=p.get('planned_time'),task__date=day,
            task__exploitation=farm,task__assigned_to_id=p['assigned_to']).exclude(task__status='CANCELLED').select_related('task').first()
        if existing:tasks.append(existing.task);continue
        serializer=AgendaSerializer(data={'title':f'Vaccination — {product.name} — {lot.nom}'[:255],'date':day,
            'assigned_to':p['assigned_to'],'description':p.get('observations','')},context={'request':request,'health_planning':True})
        serializer.is_valid(raise_exception=True)
        task=serializer.save(exploitation=farm,created_by=actor)
        checked(HealthTask.objects.create,task=task,product=product,lot=lot,planned_time=p.get('planned_time'),
            original_date=day,original_time=p.get('planned_time'),series_uuid=identifier,
            interval_days=p.get('interval_days'),series_end=p.get('end_date'))
        tasks.append(task)
    checked(HealthCommand.objects.create,exploitation=farm,operation_uuid=identifier,author=actor,kind='PLAN',payload=body,
        fingerprint=digest,task_ids=[t.pk for t in tasks])
    farm.business_revision+=1;farm.save(update_fields=['business_revision'])
    return tasks


@transaction.atomic
def move(request,task,body):
    actor=request.user;farm=Exploitation.objects.select_for_update().get(pk=actor.exploitation_id)
    authorize(actor,farm,'agenda.plan',task);authorize(actor,farm,'agenda.read',task)
    if set(body)-{'operation_uuid','date','planned_time','assigned_to','observations','reason','expected_version'}:raise ValidationError('HEALTH_MOVE_FIELDS_INVALID')
    payload={**body,'task_id':task.pk};identifier,digest,prior=command(actor,payload,'MOVE')
    if prior:return Task.objects.get(pk=task.pk,exploitation=farm)
    task=Task.objects.select_for_update().get(pk=task.pk,exploitation=farm)
    meta=HealthTask.objects.filter(task=task).first()
    if meta is None or task.status=='CANCELLED':raise ValidationError('ACTIVE_HEALTH_TASK_REQUIRED')
    if task.vaccination_links.filter(vaccination__voided_at__isnull=True,vaccination__animals__gt=0).exists():raise ValidationError('HEALTH_TASK_ALREADY_FULFILLED')
    ensure_open(meta.lot)
    if not isinstance(body.get('reason'),str) or not 3<=len(body['reason'].strip())<=2000:raise ValidationError('HEALTH_MOVE_REASON_REQUIRED')
    if 'date' not in body or 'expected_version' not in body:raise ValidationError('HEALTH_MOVE_DATE_VERSION_REQUIRED')
    assignee=body.get('assigned_to',task.assigned_to_id);authorized_assignee(farm.pk,assignee)
    from .task_views import AgendaSerializer
    incoming=AgendaSerializer(task,data={'date':body['date'],'assigned_to':assignee,
        'description':body.get('observations',task.description),'expected_version':body['expected_version']},partial=True,
        context={'request':request,'health_planning':True})
    incoming.is_valid(raise_exception=True);task=incoming.save(version=task.version+1)
    if 'planned_time' in body:
        meta.planned_time=serializers.TimeField(allow_null=True).run_validation(body['planned_time'])
        if meta.planned_time and meta.planned_time.tzinfo is not None:raise ValidationError('HEALTH_LOCAL_TIME_REQUIRED')
        checked(meta.save)
    checked(HealthCommand.objects.create,exploitation=farm,operation_uuid=identifier,author=actor,kind='MOVE',payload=payload,
        fingerprint=digest,task_ids=[task.pk])
    farm.business_revision+=1;farm.save(update_fields=['business_revision'])
    return task


def summary(task):
    meta=HealthTask.objects.select_related('product','lot').filter(task=task).first()
    if meta is None:return None
    at=timezone.make_aware(datetime.combine(task.date,meta.planned_time)) if meta.planned_time else None
    actuals=list(task.vaccination_links.select_related('vaccination__created_by').order_by('pk'))
    active=next((link.vaccination for link in actuals if link.vaccination.voided_at is None and link.vaccination.animals>0),None)
    now=timezone.now()
    return {'exploitation':task.exploitation_id,'product':str(meta.product_id),'vaccine':meta.product.name,
        'lot':meta.lot_id,'lot_name':meta.lot.nom,'species':meta.lot.espece_id,'planned_time':str(meta.planned_time) if meta.planned_time else None,
        'planned_at':at.isoformat() if at else None,'timezone':str(timezone.get_default_timezone()),
        'original_date':str(meta.original_date),'original_time':str(meta.original_time) if meta.original_time else None,
        'interval_days':meta.interval_days,'series_end':str(meta.series_end) if meta.series_end else None,'series_uuid':str(meta.series_uuid),
        'fulfilled':active is not None,'overdue':task.status!='CANCELLED' and active is None and (now>at if at else timezone.localdate(now)>task.date),
        'actual':({'vaccination':active.pk,'date':active.vaccinated_at.isoformat(),'operator':active.created_by_id,
            'operator_name':active.created_by.username,'difference_days':(timezone.localdate(active.vaccinated_at)-task.date).days,
            'difference_seconds':int((active.vaccinated_at-at).total_seconds()) if at else None} if active else None),
        'administration_history':[{'vaccination':link.vaccination_id,'voided':link.vaccination.voided_at is not None} for link in actuals]}


def validate_link(actor,item,task_id,expected_version):
    from .vaccine_services import actor_guard,integer
    from .audit import current_actor_context
    from .models import TerrainSubmission
    task=Task.objects.select_for_update().filter(pk=integer(task_id,1),exploitation_id=actor.exploitation_id).first()
    if task is None:raise ValidationError('HEALTH_TASK_OUTSIDE_FARM')
    ctx=current_actor_context()
    if ctx.source=='OFFLINE':
        original=TerrainSubmission.objects.select_related('offline_authorization').get(exploitation_id=actor.exploitation_id,client_operation_id=ctx.operation_id)
        if original.offline_authorization.capabilities.get('health_agenda_contract')!=1:raise PermissionDenied('HEALTH_GRANT_RENEWAL_REQUIRED')
        # The signed root remains VACCINATION/CREATE. Additional rights are
        # verified against that original grant and current membership, without
        # pretending the declaration is a different primary operation.
        actor_guard(actor,'vaccinations.create')
        from .offline_permissions import fine_contract
        grant=original.offline_authorization
        codes=fine_contract(grant)
        if codes is None or not {'agenda.read','agenda.progress'}<=codes or not {'agenda.read','agenda.progress'}<=effective_permissions(grant.membership):
            raise PermissionDenied('HEALTH_LINK_AGENDA_PERMISSION_REQUIRED')
        if task.assigned_to_id!=actor.pk:raise PermissionDenied('HEALTH_TASK_OTHER_ASSIGNEE')
    else:
        authorize(actor,actor.exploitation_id,'agenda.read',task)
        if actor.pk==task.exploitation.proprietaire_id:authorize(actor,actor.exploitation_id,'agenda.plan',task)
        else:
            authorize(actor,actor.exploitation_id,'agenda.progress',task)
            if task.assigned_to_id!=actor.pk:raise PermissionDenied('HEALTH_TASK_OTHER_ASSIGNEE')
    meta=HealthTask.objects.filter(task=task,product=item.product,lot=item.lot).first()
    if meta is None or task.status=='CANCELLED':raise ValidationError('HEALTH_TASK_MISMATCH_OR_CANCELLED')
    if type(expected_version) is not int or task.version!=expected_version:raise ValidationError('HEALTH_TASK_VERSION_CONFLICT')
    if task.vaccination_links.filter(vaccination__voided_at__isnull=True,vaccination__animals__gt=0).exists():raise ValidationError('HEALTH_TASK_ALREADY_FULFILLED')
    return task


def task_cancel_candidate(row):
    """Only classify an unapplied sanitary report; never re-approve its effect."""
    return (row.entity_type=='TASK' and row.operation_type=='UPDATE'
        and row.offline_authorization.capabilities.get('health_agenda_contract')==1
        and type(row.payload.get('task_id')) is int
        and HealthTask.objects.filter(task_id=row.payload['task_id'],task__exploitation_id=row.exploitation_id).exists())
