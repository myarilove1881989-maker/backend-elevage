import hashlib
import json
from functools import wraps
from django.core import signing
from django.core.exceptions import ValidationError as ModelError
from django.db import IntegrityError, transaction
from django.db.models import Sum, Max, Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.exceptions import APIException, ValidationError, NotFound
from core.access_policy import active_membership
from core.permission_catalog import AQUATIC_PERMISSIONS
from . import serializers as inputs
from .models import Activity, Installation, AquaticSpeciesProfile, Stage, Cohort, Position, Event, Command, CountLine
from .services import access, enabled, execute, public, get, etag


def endpoint(methods):
    def decorate(fn):
        @api_view(methods)
        @permission_classes([IsAuthenticated])
        @wraps(fn)
        def wrapped(request, *args, **kwargs):
            try:
                return fn(request,*args,**kwargs)
            except APIException as exc:
                detail=exc.detail
                code=str(detail) if not isinstance(detail,(dict,list)) else 'VALIDATION_ERROR'
                return Response({'error':{'code':code,'message':str(detail),'fields':detail if isinstance(detail,dict) else {},'retryable':False}},status=exc.status_code)
            except (ModelError,IntegrityError) as exc:
                # Constraint names and tenant identifiers are not exposed.
                return Response({'error':{'code':'VALIDATION_ERROR','message':'Données invalides ou identifiant déjà utilisé.','retryable':False}},status=400)
        return wrapped
    return decorate


def dated(value):
    try:result=parse_datetime(value)
    except (ValueError,TypeError):raise ValidationError('INVALID_DATE')
    if not result or timezone.is_naive(result):raise ValidationError('INVALID_DATE')
    return result


@transaction.atomic
def write(request, cls, kind, pk=None):
    # Device proof signs the exact bytes; cache them before DRF parses the body.
    request.body
    serializer=cls(data=request.data);serializer.is_valid(raise_exception=True)
    from core.models import Exploitation
    from core.device_services import verify_request_device
    farm=Exploitation.objects.select_for_update().get(pk=request.user.exploitation_id)
    member=active_membership(request.user)
    device=None
    if member.role=='OPERATEUR' and farm.offline_policy_enabled:
        device=verify_request_device(request,primary=True)
    data,status,replayed=execute(request.user,kind,serializer.validated_data,pk=pk,match=request.headers.get('If-Match'),device=device)
    response=Response(data,status=status)
    if replayed:response['Idempotent-Replay']='true'
    if 'id' in data and 'version' in data:response['ETag']=f'"{data["id"]}:{data["version"]}"'
    return response


@endpoint(['GET'])
def capabilities(request):
    member=active_membership(request.user)
    on=enabled(member.exploitation_id)
    rights=AQUATIC_PERMISSIONS if member.role=='OWNER' else set(member.action_permissions.values_list('code',flat=True)) & AQUATIC_PERMISSIONS
    from core.access_policy import effective_permissions
    commerce_rights={'cash.create','clients.create'} if member.role=='OWNER' else effective_permissions(member)&{'cash.create','clients.create'}
    return Response({'commerce_write':sorted(commerce_rights) if on and 'aquatic.read' in rights else [],'contract_version':1,'enabled':on,'can_enable':member.role=='OWNER',
        'read':['aquatic.read'] if on and 'aquatic.read' in rights else [],
        'write':sorted(rights-{'aquatic.read'}) if on else [],'offline_write':[],
        'future_contracts':{'loss_reserve':'G-R','unassigned_inventory_credit_transferable':False,
            'sensitive_corrections':'OWNER_ONLY','stock_reconciliation_owner_only':True,'loss_operations':True,'inventory_operations':True}})


@endpoint(['POST'])
def activation(request):
    return write(request,inputs.Activation,'activation')


@endpoint(['POST'])
def onboarding(request):
    return write(request,inputs.Onboarding,'onboarding')


def queryset(request, model, *, filters=None):
    access(request.user,'aquatic.read')
    allowed={'cursor','limit','q','status','type','species','stage','installation','cohort','from','to','as_of'}
    if set(request.query_params)-allowed:raise ValidationError('UNKNOWN_FILTER')
    qs=model.objects.filter(exploitation_id=request.user.exploitation_id)
    try:
        for key,path in (filters or {}).items():
            value=request.query_params.get(key)
            if value:qs=qs.filter(**{path:value})
        if request.query_params.get('q') and hasattr(model,'name'):
            qs=qs.filter(name__icontains=request.query_params['q'][:120])
        # Force validation before any potentially lazy response iteration.
        str(qs.query)
    except (ValueError,TypeError):raise ValidationError('INVALID_FILTER')
    return qs


def page(request, qs, encode=public):
    try:
        limit=int(request.query_params.get('limit',50))
        if not 1<=limit<=100:raise ValueError()
    except ValueError:raise ValidationError('INVALID_LIMIT')
    scope=hashlib.sha256(json.dumps({'tenant':request.user.exploitation_id,'path':request.path,
        'filters':{k:v for k,v in request.query_params.items() if k!='cursor'}},sort_keys=True).encode()).hexdigest()
    cursor=request.query_params.get('cursor')
    if cursor:
        try:state=signing.loads(cursor,salt='aquatic-pages',max_age=86400)
        except signing.BadSignature:raise ValidationError('INVALID_CURSOR')
        if state['scope']!=scope:raise ValidationError('CURSOR_SCOPE_MISMATCH')
    else:state={'scope':scope}
    if qs.model is Event:
        if 'snapshot' not in state:state['snapshot']=qs.aggregate(n=Max('id'))['n'] or 0
        qs=qs.filter(id__lte=state['snapshot'])
        if 'at' in state:
            at=parse_datetime(state['at']);qs=qs.filter(Q(occurred_at__gt=at)|Q(occurred_at=at,id__gt=state['last']))
        qs=qs.order_by('occurred_at','id')
    else:
        if 'last' in state:qs=qs.filter(pk__gt=state['last'])
        qs=qs.order_by('pk')
    rows=list(qs[:limit+1]);more=len(rows)>limit;rows=rows[:limit]
    next_cursor=None
    if more:
        state['last']=str(rows[-1].pk)
        if qs.model is Event:state['at']=rows[-1].occurred_at.isoformat()
        next_cursor=signing.dumps(state,salt='aquatic-pages',compress=True)
    return Response({'results':[encode(row) for row in rows],'next_cursor':next_cursor})


@endpoint(['GET','POST'])
def installations(request):
    if request.method=='POST':return write(request,inputs.InstallationInput,'installation')
    return page(request,queryset(request,Installation,filters={'status':'status','type':'type'}))


@endpoint(['GET','PATCH'])
def installation(request,pk):
    if request.method=='PATCH':return write(request,inputs.InstallationInput,'installation_patch',pk)
    access(request.user,'aquatic.read');obj=get(Installation,request.user.exploitation_id,pk)
    response=Response(public(obj));response['ETag']=etag(obj);return response


@endpoint(['GET','POST'])
def species(request):
    if request.method=='POST':return write(request,inputs.SpeciesInput,'species')
    return page(request,queryset(request,AquaticSpeciesProfile).filter(active=True).select_related('species'),
        lambda row:{**public(row),'name':row.species.nom})


@endpoint(['GET','POST'])
def stages(request):
    if request.method=='POST':return write(request,inputs.StageInput,'stage')
    return page(request,queryset(request,Stage,filters={'species':'species_id'}))


@endpoint(['GET','POST'])
def cohorts(request):
    if request.method=='POST':return write(request,inputs.CohortInput,'cohort')
    qs=queryset(request,Cohort,filters={'status':'status','species':'species_id','stage':'stage_id',
        'installation':'positions__installation_id'}).select_related('species','stage').prefetch_related('positions').distinct()
    return page(request,qs)


@endpoint(['GET','PATCH'])
def cohort(request,pk):
    if request.method=='PATCH':return write(request,inputs.CohortPatch,'cohort_patch',pk)
    access(request.user,'aquatic.read');obj=get(Cohort,request.user.exploitation_id,pk)
    response=Response(public(obj));response['ETag']=etag(obj);return response


@endpoint(['GET'])
def positions(request):
    qs=queryset(request,Position,filters={'installation':'installation_id','cohort':'cohort_id'})
    at=request.query_params.get('as_of')
    if at:
        at=dated(at)
        from .models import StockFact
        if StockFact.objects.filter(exploitation_id=request.user.exploitation_id).exists():
            from .stock_ledger import replay,pending_reserve
            state=replay(request.user.exploitation_id,at)
            def historical_position(p):
                key=(str(p.cohort_id),str(p.installation_id));gross=state['gross'].get(key,0)
                reserve=min(gross,pending_reserve(state,key[1],key[0]))
                return {**public(p),'gross_count':gross,'reserved_count':reserve,'available_count':gross-reserve,
                    'count_quality':'UNRECONCILED' if reserve else 'EXACT','as_of':at.isoformat()}
            return page(request,qs,historical_position)
        return page(request,qs,lambda p:{**public(p),'gross_count':p.countline_set.filter(event__occurred_at__lte=at).aggregate(n=Sum('delta_count'))['n'] or 0,
            'as_of':at.isoformat(),'available_count':None})
    return page(request,qs)


@endpoint(['POST'])
def transfers(request):
    return write(request,inputs.Transfer,'transfer')


def event_data(row):
    return {'id':str(row.uuid),'sequence':row.pk,'type':row.type,'cohort':str(row.cohort_id),
        'occurred_at':row.occurred_at.isoformat(),'recorded_at':row.recorded_at.isoformat(),
        'actor':row.actor_id,'actor_name':row.actor.get_full_name() or row.actor.username,'reason':row.reason,'operation_uuid':str(row.command_uuid),
        'lines':[{'position':str(line.position_id),'installation':str(line.position.installation_id),
            'delta_count':line.delta_count} for line in row.lines.all()]}


@endpoint(['GET'])
def events(request):
    qs=queryset(request,Event,filters={'cohort':'cohort_id','type':'type','installation':'lines__position__installation_id'}).distinct().select_related('actor').prefetch_related('lines__position')
    for key,lookup in [('from','occurred_at__gte'),('to','occurred_at__lt')]:
        value=request.query_params.get(key)
        if value:
            date=dated(value)
            qs=qs.filter(**{lookup:date})
    return page(request,qs,event_data)


@endpoint(['GET'])
def command(request,operation_uuid):
    access(request.user,'aquatic.read')
    row=Command.objects.filter(exploitation_id=request.user.exploitation_id,operation_uuid=operation_uuid,actor=request.user).first()
    if not row:raise NotFound('NOT_FOUND')
    return Response({'status':'APPLIED','result':row.result,'response_status':row.response_status})


@endpoint(['GET'])
def dashboard(request):
    ps=queryset(request,Position,filters={'species':'cohort__species_id','cohort':'cohort_id','installation':'installation_id'})
    at=timezone.now()
    if request.query_params.get('as_of'):
        at=dated(request.query_params['as_of'])
    from .models import StockFact
    if StockFact.objects.filter(exploitation_id=request.user.exploitation_id).exists():
        from .stock_dashboard import dashboard as stock_dashboard
        from .feed_views import decorate_dashboard
        return decorate_dashboard(request,stock_dashboard(request,ps,at),at)
    lines=CountLine.objects.filter(position__in=ps,event__occurred_at__lte=at)
    total=lines.aggregate(n=Sum('delta_count'))['n'] or 0
    farm=request.user.exploitation
    installation_count=Installation.objects.filter(exploitation=farm,status='ACTIVE')
    cohort_count=Cohort.objects.filter(exploitation=farm,status='OPEN')
    if request.query_params.get('installation'):installation_count=installation_count.filter(pk=request.query_params['installation'])
    if request.query_params.get('species'):cohort_count=cohort_count.filter(species_id=request.query_params['species'])
    if request.query_params.get('cohort'):cohort_count=cohort_count.filter(pk=request.query_params['cohort'])
    if request.query_params.get('installation'):cohort_count=cohort_count.filter(positions__installation_id=request.query_params['installation']).distinct()
    if request.query_params.get('species') or request.query_params.get('cohort'):installation_count=installation_count.filter(pk__in=ps.values('installation_id'))
    by_species=[{'cohort__species_id':r['position__cohort__species_id'],'cohort__species__nom':r['position__cohort__species__nom'],'count':r['count']}
        for r in lines.values('position__cohort__species_id','position__cohort__species__nom').annotate(count=Sum('delta_count')).order_by('position__cohort__species_id')]
    by_installation=[{'installation_id':r['position__installation_id'],'installation__name':r['position__installation__name'],'count':r['count']}
        for r in lines.values('position__installation_id','position__installation__name').annotate(count=Sum('delta_count')).order_by('position__installation_id')]
    result=Response({'contract_version':1,'business_revision':farm.business_revision,'as_of':at,
        'scope':{'activity':'AQUATIC',**{k:v for k,v in request.query_params.items() if k in {'species','cohort','installation'}}},
        'metrics':{'live_count':{'value':total,'unit':'fish','quality':'EXACT'},
            'active_installations':{'value':installation_count.count(),'unit':'installation','quality':'EXACT'},
            'active_cohorts':{'value':cohort_count.count(),'unit':'cohort','quality':'EXACT'},
            **{name:{'value':None,'quality':'NOT_IMPLEMENTED','unit':unit} for name,unit in
                [('estimated_biomass','kg'),('feeding','kg'),('water',None),('mortality','fish'),('harvest','kg'),('finance',None)]}},
        'by_species':by_species,
        'by_installation':by_installation,
        'unassigned_losses':0,'capabilities':{'weighings':False,'feed_distributions':False,'water_readings':False,'losses':False,'offline_write':False}})
    from .feed_views import decorate_dashboard
    return decorate_dashboard(request,result,at)
