import uuid
from django.utils import timezone
from django.db.models import Q
from django.db import transaction
from core.models import Exploitation
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError
from core.supply_models import SupplyProduct
from .models import HealthRecord,SanitaryProduct,Installation,Cohort
from .views import endpoint,write,dated
from .services import access,get
from .feed_views import page
from .health_inputs import ObservationInput,TreatmentInput,HealthCorrectionInput,SanitaryProductInput
from .health_services import product_public,record_public,current,precaution,sale_check


def scope(request,allowed):
    member=access(request.user,'aquatic.read')
    if set(request.query_params)-allowed:raise ValidationError('UNKNOWN_HEALTH_FILTER')
    for key in ('installation','cohort'):
        if request.query_params.get(key):
            try:uuid.UUID(request.query_params[key])
            except (ValueError,TypeError):raise ValidationError('INVALID_HEALTH_FILTER')
    if request.query_params.get('species'):
        try:
            if int(request.query_params['species'])<1:raise ValueError()
        except (ValueError,TypeError):raise ValidationError('INVALID_SPECIES_FILTER')
    for key in ('from','to','as_of'):
        if request.query_params.get(key):
            value=dated(request.query_params[key])
            if key=='as_of' and value>timezone.now()+timezone.timedelta(minutes=5):raise ValidationError('FUTURE_CHECK_DATE')
    if request.query_params.get('from') and request.query_params.get('to') and dated(request.query_params['from'])>dated(request.query_params['to']):raise ValidationError('PERIOD_REVERSED')
    return member


def filtered(request,qs,period=True):
    if request.query_params.get('installation'):qs=qs.filter(installation_id=request.query_params['installation'])
    if request.query_params.get('cohort'):qs=qs.filter(eligible_cohorts__contains=[request.query_params['cohort']])
    if request.query_params.get('species'):qs=qs.filter(species_ids__contains=[int(request.query_params['species'])])
    if period:
        for key,lookup in [('from','gte'),('to','lte')]:
            if request.query_params.get(key):qs=qs.filter(**{'occurred_at__'+lookup:dated(request.query_params[key])})
    return qs

HISTORY_FILTERS={'installation','cohort','species','from','to','cursor','limit','kind'}

@endpoint(['GET','POST'])
def products(request):
    if request.method=='POST':return write(request,SanitaryProductInput,'health_product')
    member=scope(request,set())
    rows=[product_public(x) for x in SupplyProduct.objects.filter(exploitation_id=request.user.exploitation_id,kind='VACCINE').order_by('name','pk')]
    rows.extend(product_public(x) for x in SanitaryProduct.objects.filter(exploitation_id=request.user.exploitation_id).order_by('name','pk'))
    return Response({'products':rows,'can_manage':member.role=='OWNER','photo_supported':False,
        'planning_supported':False,'stock_automation':False,
        'notice':'Traitements effectivement administrés uniquement. Aucune dépense ni consommation de stock sanitaire automatique.'})


def history_response(request,kind=None):
    scope(request,HISTORY_FILTERS)
    kind=kind or request.query_params.get('kind')
    if kind and kind not in ('OBSERVATION','TREATMENT'):raise ValidationError('INVALID_HEALTH_KIND')
    qs=filtered(request,HealthRecord.objects.filter(exploitation_id=request.user.exploitation_id).select_related('actor'))
    if kind:qs=qs.filter(kind=kind)
    return page(request,qs,record_public,'health-'+(kind or 'history'))

@endpoint(['GET','POST'])
def observations(request):
    if request.method=='POST':return write(request,ObservationInput,'health_observation')
    return history_response(request,'OBSERVATION')

@endpoint(['GET','POST'])
def treatments(request):
    if request.method=='POST':return write(request,TreatmentInput,'health_treatment')
    return history_response(request,'TREATMENT')

@endpoint(['GET'])
def history(request):return history_response(request)

@endpoint(['GET'])
def record(request,pk):
    scope(request,set())
    return Response(record_public(get(HealthRecord,request.user.exploitation_id,pk)))

@endpoint(['POST'])
def corrections(request):return write(request,HealthCorrectionInput,'health_correction')

@endpoint(['GET'])
def state(request):
    scope(request,{'installation','cohort','species','cursor','limit'})
    at=timezone.now();qs=filtered(request,current(request.user.exploitation_id),period=False)
    qs=qs.filter(Q(ongoing=True)|Q(withdrawal_until__isnull=True)|Q(withdrawal_until__gt=at))
    revision=Exploitation.objects.values_list('business_revision',flat=True).get(pk=request.user.exploitation_id)
    result=page(request,qs,record_public,'health-current-precautions:'+str(revision))
    result.data.update(as_of=at.isoformat(),period_independent=True,sale_authorized=None,
        notice='Précautions actuelles, indépendantes de la période de l’historique. Pas d’autorisation de vente.')
    return result

@endpoint(['GET'])
@transaction.atomic
def sale_verification(request):
    scope(request,{'installation','cohort','as_of'})
    if not request.query_params.get('installation'):raise ValidationError('INSTALLATION_REQUIRED')
    farm=request.user.exploitation_id
    Exploitation.objects.select_for_update().get(pk=farm)
    access(request.user,'aquatic.read')
    site=get(Installation,farm,request.query_params['installation'])
    cohort=get(Cohort,farm,request.query_params['cohort']) if request.query_params.get('cohort') else None
    return Response(sale_check(request.user,site,cohort,dated(request.query_params['as_of']) if request.query_params.get('as_of') else timezone.now()))
