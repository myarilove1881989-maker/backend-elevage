import hashlib,json
from django.core import signing
from django.db.models import Max,Q
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError
from core.models import Exploitation
from .views import endpoint,write,dated
from .services import access,public
from .models import StockFact,Position,Installation
from .stock_inputs import MortalityInput,ReconcileInput,InventoryInput,AssignCreditInput,CancelInput
from .stock_ledger import replay,counts,pending_reserve,stock_public

def revision(farm):
    return Exploitation.objects.values_list('business_revision',flat=True).get(pk=farm)

def scoped(request):
    access(request.user,'aquatic.read')
    allowed={'installation','cohort','as_of','from','to','cursor','limit'}
    if set(request.query_params)-allowed:raise ValidationError('UNKNOWN_FILTER')
    # Explicit UUID validation, even when there are no matching rows.
    import uuid
    for key in ('installation','cohort'):
        if request.query_params.get(key):
            try:uuid.UUID(request.query_params[key])
            except (ValueError,TypeError):raise ValidationError('INVALID_FILTER')
    return request.user.exploitation_id

def state_data(farm,at=None):
    state=replay(farm,at)
    sites=[]
    for site in Installation.objects.filter(exploitation_id=farm).order_by('code'):
        sites.append({'id':str(site.pk),'name':site.name,**counts(state,str(site.pk))})
    positions=[]
    for p in Position.objects.filter(exploitation_id=farm).select_related('cohort','installation'):
        key=(str(p.cohort_id),str(p.installation_id));gross=state['gross'].get(key,0)
        reserve=min(gross,pending_reserve(state,key[1],key[0]))
        positions.append({'id':str(p.pk),'cohort':key[0],'cohort_name':p.cohort.name,
            'installation':key[1],'installation_name':p.installation.name,'gross_count':gross,
            'reserved_count':reserve,'available_count':max(0,gross-reserve),'version':p.version})
    return {'business_revision':revision(farm),'installations':sites,'positions':positions,
        'pending':[p for p in state['pending'].values() if p['remaining']],
        'credits':[p for p in state['credits'].values() if p['remaining']],
        'live_count':sum(x['live_count'] for x in sites),
        'unassigned_losses':sum(x['pending_count'] for x in sites),
        'unassigned_credits':sum(x['unassigned_credit'] for x in sites)}

@endpoint(['GET'])
def stock_state(request):
    farm=scoped(request)
    at=dated(request.query_params['as_of']) if request.query_params.get('as_of') else None
    result=state_data(farm,at)
    site=request.query_params.get('installation');cohort=request.query_params.get('cohort')
    if site:
        result['installations']=[x for x in result['installations'] if x['id']==site]
        result['positions']=[x for x in result['positions'] if x['installation']==site]
        result['pending']=[x for x in result['pending'] if x['installation']==site]
        result['credits']=[x for x in result['credits'] if x['installation']==site]
        result['live_count']=sum(x['live_count'] for x in result['installations'])
        result['unassigned_losses']=sum(x['remaining'] for x in result['pending'])
        result['unassigned_credits']=sum(x['remaining'] for x in result['credits'])
    if cohort:
        result['positions']=[x for x in result['positions'] if x['cohort']==cohort]
        result['pending']=[x for x in result['pending'] if cohort in x['eligible']]
        # Do not arbitrarily allocate unassigned losses or positive credits to one cohort.
        gross=sum(x['gross_count'] for x in result['positions'])
        unresolved=sum(x['remaining'] for x in result['pending'])
        result['cohort_count']={'gross':gross,'lower':max(0,gross-unresolved),'upper':gross,
            'quality':'UNRECONCILED' if unresolved else 'EXACT'}
        result['live_count']=None
    result['can_reconcile']=access(request.user,'aquatic.read').role=='OWNER'
    return Response(result)

def history(request,kind=None):
    farm=scoped(request)
    qs=StockFact.objects.filter(exploitation_id=farm)
    if kind:qs=qs.filter(kind=kind)
    site=request.query_params.get('installation');cohort=request.query_params.get('cohort')
    if site:qs=qs.filter(installation_id=site)
    if cohort:
        qs=qs.filter(Q(cohort_id=cohort)|Q(payload__allocations__contains=[{'cohort':cohort}])|Q(payload__eligible__contains=[cohort])|
            Q(reverses__cohort_id=cohort)|Q(reverses__payload__eligible__contains=[cohort])|
            Q(reverses__payload__allocations__contains=[{'cohort':cohort}]))
    for key,lookup in [('from','occurred_at__gte'),('to','occurred_at__lte'),('as_of','occurred_at__lte')]:
        if request.query_params.get(key):qs=qs.filter(**{lookup:dated(request.query_params[key])})
    scope=hashlib.sha256(json.dumps({'farm':farm,'kind':kind,'filters':{k:v for k,v in request.query_params.items() if k!='cursor'}},sort_keys=True).encode()).hexdigest()
    try:
        limit=int(request.query_params.get('limit',50))
        if not 1<=limit<=100:raise ValueError()
        cursor=request.query_params.get('cursor')
        if cursor:
            state=signing.loads(cursor,salt='aquatic-stock-history',max_age=86400)
            if state['scope']!=scope:raise ValueError()
        else:state={'scope':scope,'snapshot':(qs.aggregate(x=Max('recorded_at'))['x'] or timezone.now()).isoformat()}
    except (ValueError,signing.BadSignature):raise ValidationError('INVALID_CURSOR_OR_LIMIT')
    qs=qs.filter(recorded_at__lte=dated(state['snapshot']))
    if 'last' in state:
        qs=qs.filter(Q(occurred_at__gt=dated(state['at']))|Q(occurred_at=dated(state['at']),recorded_at__gt=dated(state['recorded']))|
            Q(occurred_at=dated(state['at']),recorded_at=dated(state['recorded']),pk__gt=state['last']))
    rows=list(qs.order_by('occurred_at','recorded_at','pk')[:limit+1]);more=len(rows)>limit;rows=rows[:limit]
    next_cursor=None
    if more:
        state.update(last=str(rows[-1].pk),at=rows[-1].occurred_at.isoformat(),recorded=rows[-1].recorded_at.isoformat())
        next_cursor=signing.dumps(state,salt='aquatic-stock-history',compress=True)
    current=replay(farm)
    return Response({'results':[stock_public(f,current) for f in rows],'next_cursor':next_cursor})

@endpoint(['GET'])
def stock_history(request):return history(request)

@endpoint(['GET','POST'])
def mortalities(request):
    return write(request,MortalityInput,'mortality') if request.method=='POST' else history(request,'MORTALITY')

@endpoint(['POST'])
def reconciliations(request):return write(request,ReconcileInput,'reconcile')

@endpoint(['GET','POST'])
def inventories(request):
    return write(request,InventoryInput,'inventory') if request.method=='POST' else history(request,'INVENTORY')

@endpoint(['POST'])
def assign_credit(request):return write(request,AssignCreditInput,'assign_credit')

@endpoint(['POST'])
def cancellations(request):return write(request,CancelInput,'stock_cancel')
