from django.db.models import Q, Max
from django.core import signing
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from .views import endpoint, write, queryset, dated, page
from .models import Observation, ObservationRule
from .observation_inputs import WeightInput, WaterInput, RuleInput, PARAMETERS
from .observations import serialize
from .services import public

def history(request,kind):
    qs=queryset(request,Observation,filters={'installation':'installation_id','cohort':'cohort_id'}).filter(kind=kind)
    for key,lookup in [('from','occurred_at__gte'),('to','occurred_at__lte')]:
        if request.query_params.get(key):qs=qs.filter(**{lookup:dated(request.query_params[key])})
    # Fixed snapshot and date order for reproducible history/chart pagination.
    import hashlib,json
    scope=hashlib.sha256(json.dumps({'tenant':request.user.exploitation_id,'kind':kind,
        'filters':{k:v for k,v in request.query_params.items() if k!='cursor'}},sort_keys=True).encode()).hexdigest()
    try:
        limit=int(request.query_params.get('limit',50))
        if not 1<=limit<=100:raise ValueError()
        if request.query_params.get('cursor'):
            state=signing.loads(request.query_params['cursor'],salt='aquatic-observations',max_age=86400)
            if state['scope']!=scope:raise ValueError()
        else:state={'scope':scope,'snapshot':(qs.aggregate(n=Max('recorded_at'))['n'] or __import__('django').utils.timezone.now()).isoformat()}
    except (ValueError,signing.BadSignature):raise ValidationError('INVALID_CURSOR_OR_LIMIT')
    qs=qs.filter(recorded_at__lte=dated(state['snapshot']))
    if 'last' in state:
        qs=qs.filter(Q(occurred_at__gt=dated(state['at']))|Q(occurred_at=dated(state['at']),pk__gt=state['last']))
    rows=list(qs.order_by('occurred_at','pk')[:limit+1]);more=len(rows)>limit;rows=rows[:limit]
    cursor=None
    if more:
        state.update(last=str(rows[-1].pk),at=rows[-1].occurred_at.isoformat())
        cursor=signing.dumps(state,salt='aquatic-observations',compress=True)
    return Response({'results':[serialize(r) for r in rows],'next_cursor':cursor})

@endpoint(['GET','POST'])
def weights(request):
    return write(request,WeightInput,'weight') if request.method=='POST' else history(request,'WEIGHT')

@endpoint(['GET','POST'])
def water(request):
    return write(request,WaterInput,'water') if request.method=='POST' else history(request,'WATER')

@endpoint(['GET','POST'])
def rules(request):
    if request.method=='POST':return write(request,RuleInput,'observation_rule')
    return page(request,queryset(request,ObservationRule,filters={'installation':'installation_id','species':'species_id','stage':'stage_id'}),
        lambda r:{**public(r),'unit':PARAMETERS[r.parameter]})
