from functools import wraps
from datetime import datetime,time,timedelta
from django.db import IntegrityError
from django.core.exceptions import ValidationError as ModelError
from django.utils import timezone
from rest_framework.decorators import api_view,permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import APIException,PermissionDenied,ValidationError
from rest_framework.response import Response
from core.access_policy import active_membership
from core.models import Espece
from .models import TechnicalParameter,ParameterRule,TechnicalReading,BreederIdentity,ReproductionCycle,ReproductionEvent
from . import services as s
from .catalog import family,REPRODUCTION_FAMILIES

def endpoint(methods):
    def outer(fn):
        @api_view(methods)
        @permission_classes([IsAuthenticated])
        @wraps(fn)
        def wrapped(request,*args,**kwargs):
            try:
                member=active_membership(request.user)
                if fn.__name__!='capabilities' and member.role!='OWNER':raise PermissionDenied('OWNER_REQUIRED')
                return fn(request,*args,**kwargs)
            except APIException as exc:return Response({'error':{'message':str(exc.detail),'code':str(exc.default_code)}},status=exc.status_code)
            except (ModelError,IntegrityError):return Response({'error':{'message':'Données invalides ou référence déjà utilisée.','code':'VALIDATION_ERROR'}},status=400)
        return wrapped
    return outer

def write(request,kind):
    result,replayed=s.execute(request.user,kind,request.data)
    r=Response(result,status=200 if replayed else 201)
    if replayed:r['Idempotent-Replay']='true'
    return r

def paginate(request,query,encode):
    try:
        limit=int(request.query_params.get('limit','50'));offset=int(request.query_params.get('cursor','0'))
        if not 1<=limit<=100 or not 0<=offset<=10000:raise ValueError()
    except (ValueError,TypeError):raise ValidationError('INVALID_PAGINATION')
    rows=list(query[offset:offset+limit+1]);return Response({'contract_version':1,'results':[encode(x) for x in rows[:limit]],'next_cursor':str(offset+limit) if len(rows)>limit else None})

def date_filter(request,query,field):
    for key,op in [('from','gte'),('to','lte')]:
        v=request.query_params.get(key)
        if v:
            if field=='occurred_at':
                d=timezone.make_aware(datetime.combine(s.day(v),time.min if key=='from' else time.max)) if len(v)==10 else s.instant(v)
            else:d=s.day(v)
            query=query.filter(**{field+'__'+op:d})
    return query

@endpoint(['GET'])
def capabilities(request):
    member=active_membership(request.user);owner=member.role=='OWNER'
    species=Espece.objects.filter(exploitation_id=member.exploitation_id).order_by('id')
    return Response({'contract_version':1,'owner':owner,'read':owner,'write':owner,'offline_write':False,'species':[{'id':x.pk,'name':x.nom,'family':family(x),'reproduction':family(x) in REPRODUCTION_FAMILIES} for x in species if family(x)]})

@endpoint(['POST'])
def setup(request):return write(request,'setup')
@endpoint(['GET','POST'])
def parameters(request):
    if request.method=='POST':return write(request,'parameter')
    species=s.species_for(request.user.exploitation_id,request.query_params.get('species'))
    return paginate(request,TechnicalParameter.objects.filter(exploitation=species.exploitation,species=species).order_by('predefined','code','id'),s.public_parameter)
@endpoint(['GET','POST'])
def rules(request):
    if request.method=='POST':return write(request,'rule')
    lot=s.lot_for(request.user.exploitation_id,request.query_params.get('lot'))
    return paginate(request,ParameterRule.objects.filter(exploitation=lot.exploitation,lot=lot).order_by('-valid_from','-version','id'),s.public_rule)
@endpoint(['GET','POST'])
def readings(request):
    if request.method=='POST':return write(request,'reading')
    lot=s.lot_for(request.user.exploitation_id,request.query_params.get('lot'))
    query=TechnicalReading.objects.filter(exploitation=lot.exploitation,lot=lot).order_by('occurred_at','id')
    return paginate(request,date_filter(request,query,'occurred_at'),s.public_reading)
@endpoint(['GET'])
def evolution(request):
    lot=s.lot_for(request.user.exploitation_id,request.query_params.get('lot'));parameter=s.get(TechnicalParameter,request.user.exploitation_id,request.query_params.get('parameter'))
    if parameter.species_id!=lot.espece_id or parameter.kind!='NUMBER':raise ValidationError('NUMERIC_PARAMETER_REQUIRED')
    query=TechnicalReading.objects.filter(exploitation=lot.exploitation,lot=lot).order_by('occurred_at','id')
    return paginate(request,date_filter(request,query,'occurred_at'),lambda r:{'id':str(r.pk),'occurred_at':r.occurred_at.isoformat(),'value':next((v for v in r.values if v['parameter']==str(parameter.pk)),None)})
@endpoint(['GET','POST'])
def identities(request):
    if request.method=='POST':return write(request,'identity')
    species=s.species_for(request.user.exploitation_id,request.query_params.get('species'),True)
    return paginate(request,BreederIdentity.objects.filter(exploitation=species.exploitation,species=species).order_by('normalized_label','id'),s.public_identity)
@endpoint(['GET','POST'])
def cycles(request):
    if request.method=='POST':return write(request,'cycle')
    lot=s.lot_for(request.user.exploitation_id,request.query_params.get('lot'),True)
    query=ReproductionCycle.objects.filter(exploitation=lot.exploitation,lot=lot).select_related('lot__espece','identity').order_by('-started_on','id')
    if request.query_params.get('identity'):
        identity=s.get(BreederIdentity,request.user.exploitation_id,request.query_params['identity']);query=query.filter(identity=identity)
    if request.query_params.get('status'):
        if request.query_params['status'] not in ['OPEN','CLOSED']:raise ValidationError('INVALID_STATUS')
        query=query.filter(status=request.query_params['status'])
    return paginate(request,query,s.public_cycle)
@endpoint(['GET','POST'])
def events(request):
    if request.method=='POST':return write(request,'event')
    farm=request.user.exploitation_id
    if request.query_params.get('cycle'):
        cycle=s.get(ReproductionCycle,farm,request.query_params['cycle']);query=ReproductionEvent.objects.filter(exploitation_id=farm,cycle=cycle)
    else:
        lot=s.lot_for(farm,request.query_params.get('lot'),True);query=ReproductionEvent.objects.filter(exploitation_id=farm,cycle__lot=lot)
    return paginate(request,date_filter(request,query.order_by('occurred_on','recorded_at','id'),'occurred_on'),s.public_event)
