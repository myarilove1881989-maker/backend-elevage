import uuid
from django.core import signing
from django.db import transaction
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError
from core.models import Exploitation,Client,Vente
from .models import Harvest,HarvestMovement,Cohort,Installation
from .services import access,get
from .views import endpoint,write,dated
from .feed_views import page
from .commerce import harvest_public,movement_public,check,invoice,balance
from . import commerce_inputs as inputs


def scope(request,allowed=None):
    access(request.user,'aquatic.read')
    allowed=allowed if allowed is not None else {'installation','cohort','species','client','status','from','to','cursor','limit','harvest','kind'}
    if set(request.query_params)-allowed:raise ValidationError('UNKNOWN_COMMERCE_FILTER')
    for key in ('installation','cohort','harvest'):
        if request.query_params.get(key):
            try:uuid.UUID(request.query_params[key])
            except ValueError:raise ValidationError('INVALID_COMMERCE_FILTER')
    for key in ('species','client'):
        if request.query_params.get(key):
            try:
                if int(request.query_params[key])<1:raise ValueError()
            except ValueError:raise ValidationError('INVALID_COMMERCE_FILTER')
    for key in ('from','to','as_of'):
        if request.query_params.get(key):dated(request.query_params[key])
    if request.query_params.get('from') and request.query_params.get('to') and dated(request.query_params['from'])>dated(request.query_params['to']):raise ValidationError('PERIOD_REVERSED')

def filtered(request,qs,movement=False):
    prefix='harvest__' if movement else ''
    for key in ('installation','cohort','species'):
        if request.query_params.get(key):qs=qs.filter(**{prefix+key+'_id':request.query_params[key]})
    if request.query_params.get('harvest'):qs=qs.filter(**{('harvest_id' if movement else 'pk'):request.query_params['harvest']})
    if request.query_params.get('client'):qs=qs.filter(**{('sale__client_id' if movement else 'movements__sale__client_id'):request.query_params['client']})
    for key,op in [('from','gte'),('to','lte')]:
        if request.query_params.get(key):qs=qs.filter(**{'occurred_at__'+op:dated(request.query_params[key])})
    if request.query_params.get('status'):
        if request.query_params['status'] not in ('AVAILABLE','EXHAUSTED','CANCELED'):raise ValidationError('INVALID_COMMERCE_STATUS')
        hs=Harvest.objects.filter(exploitation_id=request.user.exploitation_id)
        ids=[x.pk for x in hs if balance(x)['status']==request.query_params['status']]
        qs=qs.filter(**{prefix+'pk__in':ids})
    return qs.distinct()

@endpoint(['GET','POST'])
def harvests(request):
    if request.method=='POST':return write(request,inputs.HarvestInput,'harvest')
    scope(request);qs=filtered(request,Harvest.objects.filter(exploitation_id=request.user.exploitation_id).select_related('cohort','species','installation','actor'))
    revision=Exploitation.objects.values_list('business_revision',flat=True).get(pk=request.user.exploitation_id)
    return page(request,qs,harvest_public,'harvests:'+str(revision))

@endpoint(['GET'])
def harvest(request,pk):
    scope(request,set());row=get(Harvest,request.user.exploitation_id,pk)
    access(request.user,'finance.read')
    return Response({**harvest_public(row),'movements':[movement_public(m) for m in row.movements.order_by('occurred_at','recorded_at','pk')]})

@endpoint(['GET','POST'])
def sales(request):
    if request.method=='POST':return write(request,inputs.SaleInput,'harvest_sale')
    scope(request);access(request.user,'finance.read')
    qs=filtered(request,HarvestMovement.objects.filter(exploitation_id=request.user.exploitation_id,kind='SALE'),True)
    revision=Exploitation.objects.values_list('business_revision',flat=True).get(pk=request.user.exploitation_id)
    return page(request,qs,movement_public,'harvest-sales:'+str(revision))

@endpoint(['POST'])
def losses(request):return write(request,inputs.LossInput,'harvest_loss')
@endpoint(['GET'])
def history(request):
    scope(request);access(request.user,'finance.read');qs=filtered(request,HarvestMovement.objects.filter(exploitation_id=request.user.exploitation_id),True)
    if request.query_params.get('kind'):
        if request.query_params['kind'] not in ('SALE','LOSS','VOID','CORRECTION'):raise ValidationError('INVALID_COMMERCE_KIND')
        qs=qs.filter(kind=request.query_params['kind'])
    revision=Exploitation.objects.values_list('business_revision',flat=True).get(pk=request.user.exploitation_id)
    return page(request,qs,movement_public,'commerce-history:'+str(revision))
@endpoint(['POST'])
def payments(request):return write(request,inputs.PaymentInput,'commerce_payment')
@endpoint(['POST'])
def cancellations(request):return write(request,inputs.VoidInput,'commerce_void')
@endpoint(['POST'])
def corrections(request):return write(request,inputs.CorrectionInput,'commerce_correction')
@endpoint(['GET'])
def bill(request,pk):
    scope(request,set());access(request.user,'finance.read')
    row=Vente.objects.filter(pk=pk,shared_exploitation_id=request.user.exploitation_id).first()
    if not row:from rest_framework.exceptions import NotFound;raise NotFound('NOT_FOUND')
    return Response(invoice(row))
@endpoint(['GET'])
@transaction.atomic
def verification(request):
    scope(request,{'installation','cohort','harvest','as_of'})
    if not request.query_params.get('as_of'):raise ValidationError('AS_OF_REQUIRED')
    Exploitation.objects.select_for_update().get(pk=request.user.exploitation_id);access(request.user,'aquatic.read')
    h=get(Harvest,request.user.exploitation_id,request.query_params['harvest']) if request.query_params.get('harvest') else None
    if not h and not {'installation','cohort'}<=set(request.query_params):raise ValidationError('ORIGIN_REQUIRED')
    site=h.installation if h else get(Installation,request.user.exploitation_id,request.query_params['installation'])
    cohort=h.cohort if h else get(Cohort,request.user.exploitation_id,request.query_params['cohort'])
    return Response(check(request.user,site,cohort,dated(request.query_params['as_of']),h))

@endpoint(['GET','POST'])
def clients(request):
    if request.method=='POST':return write(request,inputs.ClientInput,'commerce_client')
    scope(request,{'cursor','limit'});access(request.user,'clients.read')
    try:
        limit=int(request.query_params.get('limit',50));assert 1<=limit<=100
        last=0
        if request.query_params.get('cursor'):
            state=signing.loads(request.query_params['cursor'],salt='aquatic-customers',max_age=86400)
            assert state['farm']==request.user.exploitation_id;last=state['last']
    except (ValueError,AssertionError,signing.BadSignature,KeyError):raise ValidationError('INVALID_CURSOR_OR_LIMIT')
    rows=list(Client.objects.filter(exploitation_id=request.user.exploitation_id,pk__gt=last).order_by('pk')[:limit+1]);more=len(rows)>limit;rows=rows[:limit]
    return Response({'results':[{'id':x.pk,'nom':x.nom,'telephone':x.telephone,'pays':x.pays,'ville':x.ville} for x in rows],'next_cursor':signing.dumps({'farm':request.user.exploitation_id,'last':rows[-1].pk},salt='aquatic-customers') if more else None})
