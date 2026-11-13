from django.db.models import Q
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError
from core.models import Exploitation
from .models import BroodGroup,Reproduction,BreedingFact
from .views import endpoint,write,dated
from .services import access,get
from .feed_views import page
from . import breeding_inputs as inputs
from .breeding_services import group_public,reproduce_public,fact_public,timeline

ALLOWED={'installation','species','group','reproduction','kind','status','from','to','cursor','limit'}

def scope(request):
    access(request.user,'aquatic.read')
    if set(request.query_params)-ALLOWED:raise ValidationError('UNKNOWN_BREEDING_FILTER')
    import uuid
    for key in ('installation','group','reproduction'):
        if request.query_params.get(key):
            try:uuid.UUID(request.query_params[key])
            except (ValueError,TypeError):raise ValidationError('INVALID_BREEDING_FILTER')
    if request.query_params.get('species'):
        try:
            if int(request.query_params['species'])<1:raise ValueError()
        except (ValueError,TypeError):raise ValidationError('INVALID_SPECIES_FILTER')
    for key in ('from','to'):
        if request.query_params.get(key):dated(request.query_params[key])
    if request.query_params.get('from') and request.query_params.get('to') and dated(request.query_params['from'])>dated(request.query_params['to']):raise ValidationError('PERIOD_REVERSED')


def filtered(request,qs,kind):
    prefix='reproduction__' if kind=='fact' else ''
    for key in ('installation','species'):
        if request.query_params.get(key):qs=qs.filter(**{('installation_id' if key=='installation' else prefix+'species_id'):request.query_params[key]})
    if request.query_params.get('group'):qs=qs.filter(**{('pk' if kind=='group' else prefix+'group_id'):request.query_params['group']})
    if request.query_params.get('reproduction'):qs=qs.filter(**{('reproduction_id' if kind=='fact' else 'pk' if kind=='rep' else 'reproduction__pk'):request.query_params['reproduction']})
    if request.query_params.get('status'):
        status=request.query_params['status']
        allowed=['ACTIVE','INACTIVE'] if kind=='group' else ['OPEN','CLOSED']
        if status not in allowed:raise ValidationError('INVALID_BREEDING_STATUS')
        qs=qs.filter(**{prefix+'status':status})
    for key,op in [('from','gte'),('to','lte')]:
        if request.query_params.get(key):qs=qs.filter(**{'occurred_at__'+op:dated(request.query_params[key])})
    return qs.distinct()

@endpoint(['GET','POST'])
def groups(request):
    if request.method=='POST':return write(request,inputs.GroupInput,'brood_group')
    scope(request);qs=filtered(request,BroodGroup.objects.filter(exploitation_id=request.user.exploitation_id).select_related('species','installation'),'group')
    revision=Exploitation.objects.values_list('business_revision',flat=True).get(pk=request.user.exploitation_id)
    return page(request,qs,group_public,'brood-groups:'+str(revision))

@endpoint(['GET'])
def group(request,pk):
    access(request.user,'aquatic.read');return Response(group_public(get(BroodGroup,request.user.exploitation_id,pk)))

@endpoint(['POST'])
def group_revision(request):return write(request,inputs.GroupPatch,'brood_group_patch')

@endpoint(['GET','POST'])
def reproductions(request):
    if request.method=='POST':return write(request,inputs.ReproductionInput,'reproduction')
    scope(request);qs=filtered(request,Reproduction.objects.filter(exploitation_id=request.user.exploitation_id).select_related('species','group','installation'),'rep')
    revision=Exploitation.objects.values_list('business_revision',flat=True).get(pk=request.user.exploitation_id)
    return page(request,qs,reproduce_public,'reproductions:'+str(revision))

@endpoint(['GET'])
def reproduction(request,pk):
    access(request.user,'aquatic.read');row=get(Reproduction,request.user.exploitation_id,pk)
    state=timeline(row)
    return Response({**reproduce_public(row),'facts':[fact_public(f,state) for f in state['facts'].values()],
        'hatches':[{'id':k,**v} for k,v in state['hatches'].items()]})

@endpoint(['POST'])
def reproduction_revision(request):return write(request,inputs.ReproductionPatch,'reproduction_patch')

@endpoint(['GET','POST'])
def facts(request):
    if request.method=='POST':return write(request,inputs.StepInput,'breeding_step')
    scope(request);qs=filtered(request,BreedingFact.objects.filter(exploitation_id=request.user.exploitation_id).select_related('reproduction__group','reproduction__species','installation','actor'),'fact')
    if request.query_params.get('kind'):
        if request.query_params['kind'] not in ('SPAWN','INCUBATION','HATCH','COUNT','PLACEMENT','VOID'):raise ValidationError('INVALID_BREEDING_KIND')
        qs=qs.filter(kind=request.query_params['kind'])
    return page(request,qs,fact_public,'breeding-history')

@endpoint(['POST'])
def count(request):return write(request,inputs.CountInput,'hatch_count')

@endpoint(['POST'])
def place(request):return write(request,inputs.PlacementInput,'fry_placement')

@endpoint(['POST'])
def cancel(request):return write(request,inputs.VoidInput,'breeding_void')


@endpoint(['GET'])
def revisions(request):
    scope(request)
    if set(request.query_params)-{'group','reproduction','cursor','limit'} or bool(request.query_params.get('group'))==bool(request.query_params.get('reproduction')):raise ValidationError('ONE_REVISION_TARGET_REQUIRED')
    from core.models import AuditEvent
    is_group=bool(request.query_params.get('group'))
    row=get(BroodGroup if is_group else Reproduction,request.user.exploitation_id,request.query_params['group' if is_group else 'reproduction'])
    qs=AuditEvent.objects.filter(exploitation_id=request.user.exploitation_id,entity_type=row._meta.label_lower,entity_id=str(row.pk),action='UPDATE')
    allowed={'name','status','installation','installation_id','cohort','cohort_id','species','species_id','males','females','note','version'}
    def encode(item):
        return {'id':item.pk,'actor':item.actor_user_id,'occurred_at':item.applied_at.isoformat(),'reason':item.reason_text,
            'before':{k:v for k,v in item.before_data.items() if k in allowed},'after':{k:v for k,v in item.after_data.items() if k in allowed}}
    return page(request,qs,encode,'breeding-revisions',date='applied_at',recorded='received_at')
