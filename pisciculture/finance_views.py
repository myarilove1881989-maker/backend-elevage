from decimal import Decimal
import hashlib,json
from django.core import signing
import uuid
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError
from core.models import Exploitation,Depense,Lettrage,CategorieDepense,AuditEvent,User
from .models import HarvestMovement,AquaticExpenseLink,AquaticTaskPlan
from .services import access,get
from .views import endpoint,write
from .feed_views import page
from .planning import expense_public,task_public
from . import planning_inputs as inputs


def money(v):return format(v.quantize(Decimal('0.01')),'.2f')
def scope(request,agenda=False):
    access(request.user,'aquatic.read')
    if not agenda:access(request.user,'finance.read')
    allowed={'from','to','species','cohort','installation','cursor','limit','status','mine','reminders'} if agenda else {'from','to','species','cohort','installation','cursor','limit','scope'}
    if set(request.query_params)-allowed:raise ValidationError('UNKNOWN_FILTER')
    for key in ('cohort','installation'):
        if request.query_params.get(key):
            try:uuid.UUID(request.query_params[key])
            except ValueError:raise ValidationError('INVALID_FILTER')
    if request.query_params.get('species'):
        try:
            if int(request.query_params['species'])<1:raise ValueError()
        except ValueError:raise ValidationError('INVALID_FILTER')
    today=timezone.localdate()
    def day(key,default):
        raw=request.query_params.get(key)
        if not raw:return default
        try:d=parse_date(raw)
        except ValueError:raise ValidationError('INVALID_DATE')
        if not d:raise ValidationError('INVALID_DATE')
        return d
    start=day('from',today.replace(day=1));end=day('to',today)
    if start>end:raise ValidationError('PERIOD_REVERSED')
    return start,end

def audit_names(events,farm):
    # Audit IDs deliberately survive account deletion; current names stay tenant-scoped.
    return {user.pk:(user.get_full_name() or user.username) for user in User.objects.filter(exploitation_id=farm,pk__in=events.values('actor_user_id'))}


def expense_matches(d,p):
    return all(not p.get(k) or str(d.get(k))==p[k] for k in ('cohort','installation','species'))
def expenses(farm):
    from django.db.models import Prefetch
    from core.supply_models import SupplyReceipt
    return Depense.objects.filter(shared_exploitation_id=farm).select_related('categorie','aquatic_cost__cohort','aquatic_cost__installation').prefetch_related(Prefetch('physical_supply_receipts',queryset=SupplyReceipt.objects.order_by('pk'),to_attr='_aquatic_receipts'))
def sale_scope(farm,p):
    qs=HarvestMovement.objects.filter(exploitation_id=farm,kind='SALE',sale__voided_at__isnull=True)
    for k in ('species','cohort','installation'):
        if p.get(k):qs=qs.filter(**{'harvest__'+k+'_id':p[k]})
    return qs.select_related('sale__client','harvest__cohort','harvest__installation','harvest__species')

def summary_data(user,p,start,end):
    rows=list(sale_scope(user.exploitation_id,p).filter(sale__date__lte=end));by_sale={m.sale_id:m for m in rows}
    billed=sum((m.sale.montant_total for m in rows if start<=m.sale.date<=end),Decimal('0'))
    # Allocation to the same Vente determines the aquatic portion of a mixed payment.
    payments=list(Lettrage.objects.filter(vente_id__in=by_sale,payment__exploitation_id=user.exploitation_id,payment__client_id__in=[m.sale.client_id for m in rows],payment__date__lte=end).select_related('payment'))
    paid_asof=sum((x.montant for x in payments if x.payment.client_id==by_sale[x.vente_id].sale.client_id),Decimal('0'))
    received=sum((x.montant for x in payments if start<=x.payment.date<=end and x.payment.client_id==by_sale[x.vente_id].sale.client_id),Decimal('0'))
    direct=Decimal('0');aq=Decimal('0');farm=Decimal('0');categories={};out=[]
    filtered=any(p.get(k) for k in ('species','cohort','installation'))
    for row in expenses(user.exploitation_id).filter(date__gte=start,date__lte=end):
        d=expense_public(row);common=d['scope']in('AQUATIC','FARM');included=expense_matches(d,p) and not common
        if d['scope']=='AQUATIC':aq+=row.montant;included=not filtered
        elif d['scope']=='FARM':farm+=row.montant;included=False
        elif included:direct+=row.montant
        if included:
            key=str(row.categorie_id);entry=categories.setdefault(key,{'category':row.categorie_id,'name':d['category_name'],'amount':Decimal('0')});entry['amount']+=row.montant
        if included or common:out.append({**d,'included_in_result':included})
    cost=direct+(aq if not filtered else Decimal('0'))
    for entry in categories.values():entry['amount']=money(entry['amount'])
    return {'period':{'from':start.isoformat(),'to':end.isoformat()},'billed_revenue':money(billed),'aquatic_expenses':money(cost),'direct_expenses':money(direct),'aquatic_common_expenses':money(aq),'farm_common_expenses':money(farm),'received_payments':money(received),
        'client_balance_as_of':money(sum((m.sale.montant_total for m in rows),Decimal('0'))-paid_asof),'estimated_result':money(billed-cost),'categories':sorted(categories.values(),key=lambda x:(x['name'],str(x['category']))),'origin_filtered':filtered,
        'basis':{'revenue':'INVOICE_DATE_IN_PERIOD','payments':'ALLOCATION_PAYMENT_DATE_IN_PERIOD','client_balance':'ALL_MATCHING_INVOICES_AND_ALLOCATIONS_THROUGH_TO_DATE','costs':'EXPENSE_DATE_IN_PERIOD','common_in_result':not filtered,'farm_common_in_result':False},
        'limits':['Résultat indicatif, pas un bénéfice net comptable. Les charges indirectes ou non affectées ne sont pas toutes incluses.','Les coûts communs restent séparés : aucune répartition automatique entre cohortes ou installations.','Les achats alimentaires communs non affectés restent des coûts de l’exploitation entière ; une affectation explicite peut préciser leur périmètre.','Le solde clients inclut les factures antérieures à la période, à la date de fin. Les encaissements sont les seuls montants lettrés aux ventes piscicoles.'],'expenses':out}

def data_page(request,rows,kind,revision):
    context=hashlib.sha256(json.dumps({'farm':request.user.exploitation_id,'kind':kind,'revision':revision,'filters':{k:v for k,v in request.query_params.items() if k!='cursor'}},sort_keys=True).encode()).hexdigest()
    def key(d):return (d['date'],d.get('scheduled_at') or '',str(d.get('expense_id') or d['id']))
    try:
        limit=int(request.query_params.get('limit',50))
        if not 1<=limit<=100:raise ValueError()
        state=signing.loads(request.query_params['cursor'],salt='aquatic-planning-pages',max_age=86400) if request.query_params.get('cursor') else {'context':context}
        if state['context']!=context:raise ValueError()
    except (ValueError,KeyError,signing.BadSignature):raise ValidationError('INVALID_CURSOR_OR_LIMIT')
    rows=sorted(rows,key=key)
    if state.get('last'):rows=[d for d in rows if key(d)>tuple(state['last'])]
    more=len(rows)>limit;rows=rows[:limit]
    return Response({'results':rows,'next_cursor':signing.dumps({'context':context,'last':key(rows[-1])},salt='aquatic-planning-pages') if more else None,'business_revision':revision})

@endpoint(['GET'])
@transaction.atomic
def summary(request):
    start,end=scope(request);farm=Exploitation.objects.select_for_update().get(pk=request.user.exploitation_id);access(request.user,'finance.read');access(request.user,'aquatic.read')
    data=summary_data(request.user,request.query_params,start,end);data.pop('expenses');data['business_revision']=farm.business_revision;return Response(data)

@endpoint(['GET','POST'])
@transaction.atomic
def expense_list(request):
    if request.method=='POST':return write(request,inputs.ExpenseInput,'finance_expense')
    start,end=scope(request);Exploitation.objects.select_for_update().get(pk=request.user.exploitation_id);access(request.user,'aquatic.read');data=summary_data(request.user,request.query_params,start,end)['expenses']
    wanted=request.query_params.get('scope')
    if wanted:
        if wanted not in ('COHORT','INSTALLATION','AQUATIC','FARM'):raise ValidationError('INVALID_SCOPE')
        data=[d for d in data if d['scope']==wanted]
    # Use the existing signed immutable-page helper on the existing dated ledger.
    ids={d['expense_id']:d for d in data};qs=expenses(request.user.exploitation_id).filter(pk__in=ids)
    rev=Exploitation.objects.values_list('business_revision',flat=True).get(pk=request.user.exploitation_id)
    return data_page(request,data,'aquatic-expenses',rev)

@endpoint(['POST'])
def expense_edit(request):return write(request,inputs.ExpenseEdit,'finance_expense_edit')
@endpoint(['POST'])
def assign(request):return write(request,inputs.AssignInput,'finance_assign')
@endpoint(['GET'])
def categories(request):
    scope(request)
    if request.query_params:raise ValidationError('CATALOG_FILTER_NOT_SUPPORTED')
    return Response({'categories':[{'id':r.pk,'name':r.nom} for r in CategorieDepense.objects.filter(exploitation_id=request.user.exploitation_id).order_by('nom','pk')]})

@endpoint(['GET'])
@transaction.atomic
def revenues(request):
    start,end=scope(request);Exploitation.objects.select_for_update().get(pk=request.user.exploitation_id);access(request.user,'aquatic.read');qs=sale_scope(request.user.exploitation_id,request.query_params).filter(sale__date__lte=end)
    from django.db.models import Q
    paid_sales=Lettrage.objects.filter(payment__exploitation_id=request.user.exploitation_id,payment__date__gte=start,payment__date__lte=end).values('vente_id')
    qs=qs.filter(Q(sale__date__gte=start)|Q(sale_id__in=paid_sales)).distinct()
    from django.db.models import Prefetch,F
    allocations=Lettrage.objects.filter(payment__exploitation_id=request.user.exploitation_id,payment__date__lte=end,payment__client_id=F('vente__client_id')).select_related('payment')
    qs=qs.prefetch_related(Prefetch('sale__lettrages',queryset=allocations,to_attr='_aquatic_paid_asof'))
    def encode(row):
        s=row.sale;payments=s._aquatic_paid_asof
        paid=sum((x.montant for x in payments),Decimal('0'))
        return {'id':str(row.pk),'sale_id':s.pk,'invoice_reference':'FAC-'+str(s.date.year)+'-'+str(s.pk).zfill(5),'date':s.date.isoformat(),'invoice_in_period':s.date>=start,'received_in_period':money(sum((x.montant for x in payments if start<=x.payment.date<=end),Decimal('0'))),'client':s.client_id,'client_name':s.client.nom,'amount':str(s.montant_total),'paid_as_of':money(paid),'balance_as_of':money(s.montant_total-paid),'payments':[{'id':x.payment_id,'amount':str(x.montant),'date':x.payment.date.isoformat()} for x in payments],'cohort_name':row.harvest.cohort.name,'installation_name':row.harvest.installation.name,'unit':s.price_unit,'quantity':str(s.priced_quantity),'occurred_at':row.occurred_at.isoformat()}
    rev=Exploitation.objects.values_list('business_revision',flat=True).get(pk=request.user.exploitation_id)
    return page(request,qs,encode,'aquatic-revenues:'+str(rev))

@endpoint(['GET'])
def revisions(request):
    access(request.user,'aquatic.read');access(request.user,'finance.read')
    if set(request.query_params)-{'target','cursor','limit'} or not request.query_params.get('target'):raise ValidationError('TARGET_REQUIRED')
    link=get(AquaticExpenseLink,request.user.exploitation_id,request.query_params['target'])
    from django.db.models import Q
    qs=AuditEvent.objects.filter(exploitation_id=request.user.exploitation_id).filter(Q(entity_type='pisciculture.aquaticexpenselink',entity_id=str(link.pk))|Q(entity_type='core.depense',entity_id=str(link.expense_id)))
    names=audit_names(qs,request.user.exploitation_id)
    return page(request,qs,lambda x:{'id':x.pk,'actor':x.actor_user_id,'actor_name':names.get(x.actor_user_id,'Auteur conservé'),'date':x.applied_at.isoformat(),'action':x.action,'reason':x.reason_text,'before':x.before_data,'after':x.after_data},'aquatic-expense-revisions',date='applied_at',recorded='received_at')


@endpoint(['GET','POST'])
@transaction.atomic
def tasks(request):
    if request.method=='POST':return write(request,inputs.TaskInput,'aquatic_task')
    start,end=scope(request,True);farm=Exploitation.objects.select_for_update().get(pk=request.user.exploitation_id);access(request.user,'aquatic.read')
    qs=AquaticTaskPlan.objects.filter(exploitation=farm).select_related('task__created_by')
    for key in ('cohort','installation'):
        if request.query_params.get(key):qs=qs.filter(**{key+'_id':request.query_params[key]})
    if request.query_params.get('species'):qs=qs.filter(cohort__species_id=request.query_params['species'])
    for key in ('mine','reminders'):
        if request.query_params.get(key) not in (None,'true','false'):raise ValidationError('INVALID_BOOLEAN_FILTER')
    if request.query_params.get('mine')=='true':qs=qs.filter(task__created_by=request.user)
    if request.query_params.get('reminders')=='true':qs=qs.filter(task__status='TODO',reminder_at__lte=timezone.now())
    else:qs=qs.filter(task__date__gte=start,task__date__lte=end)
    if request.query_params.get('status'):
        if request.query_params['status'] not in ('TODO','DONE','CANCELLED'):raise ValidationError('INVALID_TASK_STATUS')
        qs=qs.filter(task__status=request.query_params['status'])
    return data_page(request,[task_public(p) for p in qs],'aquatic-agenda',farm.business_revision)

@endpoint(['POST'])
def task_edit(request):return write(request,inputs.TaskEdit,'aquatic_task_edit')
@endpoint(['POST'])
def task_progress(request):return write(request,inputs.Progress,'aquatic_task_progress')
@endpoint(['GET'])
def task_history(request):
    access(request.user,'aquatic.read')
    if set(request.query_params)-{'target','limit','cursor'} or not request.query_params.get('target'):raise ValidationError('TARGET_REQUIRED')
    plan=get(AquaticTaskPlan,request.user.exploitation_id,request.query_params['target'])
    from django.db.models import Q
    qs=AuditEvent.objects.filter(exploitation_id=request.user.exploitation_id).filter(Q(entity_type='pisciculture.aquatictaskplan',entity_id=str(plan.pk))|Q(entity_type='core.task',entity_id=str(plan.task_id)))
    names=audit_names(qs,request.user.exploitation_id)
    return page(request,qs,lambda x:{'id':x.pk,'actor':x.actor_user_id,'actor_name':names.get(x.actor_user_id,'Auteur conservé'),'date':x.applied_at.isoformat(),'action':x.action,'reason':x.reason_text,'before':x.before_data,'after':x.after_data},'aquatic-agenda-revisions',date='applied_at',recorded='received_at')
