"""Metadata over existing expenses and Tasks; never executes a planned business act."""
from django.utils import timezone
from rest_framework.exceptions import ValidationError,PermissionDenied
from core.models import Depense,CategorieDepense,Task,AuditEvent
from core.supply_models import SupplyReceipt
from core.access_policy import active_membership
from .models import AquaticExpenseLink,AquaticTaskPlan,Cohort,Installation,Observation,FeedDistribution,HealthRecord,Reproduction,BreedingFact,Harvest
from .services import get,fail,public,access
from .observations import metadata_at


def targets(farm,d):
    cohort=get(Cohort,farm,d.get('cohort')) if d.get('cohort') else None
    site=get(Installation,farm,d.get('installation')) if d.get('installation') else None
    return cohort,site

def expense_public(row):
    try:link=row.aquatic_cost
    except AquaticExpenseLink.DoesNotExist:link=None
    receipt=SupplyReceipt.objects.filter(expense_id=row.pk).first()
    return {'id':str(link.pk) if link else None,'expense_id':row.pk,'amount':str(row.montant),'date':row.date.isoformat(),'category':row.categorie_id,'category_name':row.categorie.nom if row.categorie_id else 'Non renseignée','note':row.note or '',
        'scope':link.scope if link else 'FARM','cohort':str(link.cohort_id) if link and link.cohort_id else None,'installation':str(link.installation_id) if link and link.installation_id else None,'species':link.species_id if link else None,
        'cohort_name':link.cohort.name if link and link.cohort_id else None,'installation_name':link.installation.name if link and link.installation_id else None,'version':link.version if link else 0,'source':'SUPPLY_RECEIPT' if receipt else 'MANUAL_EXPENSE','source_receipt':receipt.pk if receipt else None,'economic_editable':receipt is None,'unassigned':link is None,'recorded_at':link.recorded_at.isoformat() if link else None}

def task_public(plan):
    t=plan.task
    return {'id':str(plan.pk),'task_id':t.pk,'version':t.version,'title':t.title,'date':t.date.isoformat(),'kind':plan.kind,'scheduled_at':plan.scheduled_at.isoformat() if plan.scheduled_at else None,'reminder_at':plan.reminder_at.isoformat() if plan.reminder_at else None,
        'installation':str(plan.installation_id) if plan.installation_id else None,'cohort':str(plan.cohort_id) if plan.cohort_id else None,'description':t.description,'status':t.status,'actor':t.created_by_id,'actor_name':t.created_by.get_full_name() or t.created_by.username,
        'report':t.report,'completed_at':t.completed_at.isoformat() if t.completed_at else None,'cancelled_at':t.cancelled_at.isoformat() if t.cancelled_at else None,'cancellation_reason':t.cancellation_reason,
        'actual_kind':plan.actual_kind,'actual_id':str(plan.actual_id) if plan.actual_id else None,'occurred_at':plan.occurred_at.isoformat(),'recorded_at':plan.recorded_at.isoformat(),'business_effect_created':False,'notification_mode':'IN_APP_ONLY',
        'reminder_due':t.status=='TODO' and plan.reminder_at is not None and plan.reminder_at<=timezone.now()}

def apply(user,farm,args,identifier,kind):
    d=dict(args)
    if kind in ('finance_expense','finance_expense_edit','finance_assign'):
        c,site=targets(farm.pk,d);scope=d['scope']
        if kind=='finance_assign':
            if active_membership(user).role!='OWNER':raise PermissionDenied('OWNER_REQUIRED')
            row=Depense.objects.filter(pk=d['expense'],shared_exploitation=farm).first()
            if row is None:from rest_framework.exceptions import NotFound;raise NotFound('NOT_FOUND')
            link=AquaticExpenseLink.objects.filter(expense=row).first()
            if d['expected_version']!=(link.version if link else 0):fail('STALE_RESOURCE')
        else:
            link=get(AquaticExpenseLink,farm.pk,d['target']) if kind=='finance_expense_edit' else None
            if link and link.version!=d['expected_version']:fail('STALE_RESOURCE')
            row=link.expense if link else Depense(shared_exploitation=farm)
            if link and SupplyReceipt.objects.filter(expense=row).exists():fail('SUPPLY_EXPENSE_USE_ORIGINAL_CORRECTION')
            if 'new_category'in d:
                if active_membership(user).role!='OWNER':raise PermissionDenied('OWNER_REQUIRED_FOR_CATALOG')
                if CategorieDepense.objects.filter(exploitation=farm,nom__iexact=d['new_category']).exists():fail('EXPENSE_CATEGORY_ALREADY_EXISTS')
                category=CategorieDepense.objects.create(exploitation=farm,nom=d['new_category'])
            else:category=get(CategorieDepense,farm.pk,d['category'])
            row.categorie=category;row.montant=d['amount'];row.date=d['date'];row.note=d['note'];row.shared_purpose='FARM' if scope=='FARM' else 'AQUATIC'
            row.full_clean();row.save()
        link=link or AquaticExpenseLink(exploitation=farm,expense=row,actor=user)
        if link.pk and not link._state.adding:link.version+=1
        link.scope=scope;link.cohort=c;link.installation=site
        link.species_id=metadata_at(c,timezone.datetime.combine(row.date,timezone.datetime.min.time(),tzinfo=timezone.get_current_timezone()))[0]['species_id'] if c else None
        link.full_clean();link.save();return expense_public(row)
    if kind in ('aquatic_task','aquatic_task_edit'):
        plan=get(AquaticTaskPlan,farm.pk,d['target']) if kind=='aquatic_task_edit' else None
        t=plan.task if plan else Task(exploitation=farm,created_by=user,assigned_to=user)
        if plan and t.version!=d['expected_version']:fail('STALE_RESOURCE')
        if plan and active_membership(user).role!='OWNER' and t.created_by_id!=user.pk:raise PermissionDenied('TASK_AUTHOR_REQUIRED')
        if plan and t.status!='TODO':fail('TASK_FINISHED_IMMUTABLE_USE_STATUS')
        c,site=targets(farm.pk,d)
        t.title=d['title'];t.date=d['date'];t.description=d['description'];t.version+=int(plan is not None);t.full_clean();t.save()
        plan=plan or AquaticTaskPlan(exploitation=farm,task=t,actor=user)
        for name in ('kind','scheduled_at','reminder_at'):setattr(plan,name,d[name])
        plan.cohort=c;plan.installation=site;plan.version=t.version;plan.full_clean();plan.save();return task_public(plan)
    plan=get(AquaticTaskPlan,farm.pk,d['target']);t=plan.task
    if t.version!=d['expected_version']:fail('STALE_RESOURCE')
    status=d['status'];owner=active_membership(user).role=='OWNER'
    if not owner and t.assigned_to_id!=user.pk:raise PermissionDenied('TASK_ASSIGNEE_REQUIRED')
    if status in ('CANCELLED','TODO') and not owner:raise PermissionDenied('OWNER_REQUIRED')
    if t.status=='CANCELLED':fail('CANCELLED_TASK_IMMUTABLE')
    if t.status=='DONE' and status!='TODO':fail('TASK_ALREADY_DONE')
    actual=None
    if d['actual_id']:
        allowed={'WATER':Observation,'WEIGHT':Observation,'FEED':FeedDistribution,'OBSERVATION':HealthRecord,'TREATMENT':HealthRecord,'REPRODUCTION':Reproduction,'HATCH':BreedingFact,'HARVEST':Harvest}
        actual=get(allowed[d['actual_kind']],farm.pk,d['actual_id'])
        if hasattr(actual,'kind') and actual.kind!=d['actual_kind']:raise ValidationError('TASK_ACT_KIND_MISMATCH')
        expected={'HATCH_CHECK':'HATCH'}.get(plan.kind,plan.kind)
        if plan.kind!='OTHER' and expected!=d['actual_kind']:raise ValidationError('TASK_ACT_KIND_MISMATCH')
        site_id=getattr(actual,'installation_id',None);cohort_id=getattr(actual,'cohort_id',None)
        if isinstance(actual,Reproduction):cohort_id=actual.group_snapshot.get('cohort')
        contexts=[]
        if isinstance(actual,Observation):
            contexts=actual.result.get('population_context',actual.result.get('count_reference',{})).get('positions',[])
        if plan.installation_id and str(site_id)!=str(plan.installation_id) and not any(str(x.get('installation'))==str(plan.installation_id) for x in contexts):raise ValidationError('TASK_ACT_SCOPE_MISMATCH')
        if plan.cohort_id and str(cohort_id)!=str(plan.cohort_id) and str(plan.cohort_id) not in getattr(actual,'eligible_cohorts',[]) and not any(str(x.get('cohort'))==str(plan.cohort_id) and (not plan.installation_id or str(x.get('installation'))==str(plan.installation_id)) for x in contexts):raise ValidationError('TASK_ACT_SCOPE_MISMATCH')
    t.status=status;t.report=d['report'];t.version+=1
    t.completed_by=user if status=='DONE' else None;t.completed_at=timezone.now() if status=='DONE' else None
    if status=='CANCELLED':t.cancelled_by=user;t.cancelled_at=timezone.now();t.cancellation_reason=d['reason']
    t.save();plan.version=t.version;plan.actual_kind=d['actual_kind'];plan.actual_id=d['actual_id'];plan.full_clean();plan.save();return task_public(plan)
