import hashlib,json,uuid
from decimal import Decimal,InvalidOperation
from datetime import date,datetime,time
from django.db import transaction
from django.core.exceptions import ValidationError as ModelError
from django.db.models import Sum
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.exceptions import ValidationError,NotFound,PermissionDenied,APIException
from core.models import Exploitation,Espece,Lot,Mouvement,TerrainStockAdjustment
from core.audit import ActorContext,audit_scope
from core.access_policy import active_membership
from core.lot_lifecycle import ensure_open
from .models import Command,TechnicalParameter,ParameterRule,TechnicalReading,BreederIdentity,ReproductionCycle,ReproductionEvent
from .catalog import family,templates,REPRODUCTION_FAMILIES

class Conflict(APIException):status_code=409;default_detail='VERSION_CONFLICT'
def fail(code):raise ValidationError(code)
def integer(v,minimum=0):
    if type(v) is not int or not minimum<=v<=2147483647:fail('INTEGER_REQUIRED')
    return v
def boolean(v):
    if type(v) is not bool:fail('BOOLEAN_REQUIRED')
    return v
def decimal(v):
    if isinstance(v,bool) or not isinstance(v,(str,int,Decimal)):fail('DECIMAL_STRING_REQUIRED')
    try:d=Decimal(str(v))
    except (InvalidOperation,ValueError):fail('INVALID_NUMBER')
    if not d.is_finite() or abs(d)>=Decimal('100000000000000') or d!=d.quantize(Decimal('.000001')):fail('INVALID_DECIMAL_PRECISION')
    return d
def label(v,max_length=120,optional=False):
    if not isinstance(v,str) or len(v)>max_length or (not optional and not v.strip()):fail('INVALID_TEXT')
    return v.strip()
def day(v):
    try:
        if not isinstance(v,str) or len(v)!=10:raise ValueError()
        return date.fromisoformat(v)
    except (ValueError,TypeError):fail('INVALID_DATE')
def instant(v):
    try:d=parse_datetime(v) if isinstance(v,str) else None
    except (ValueError,TypeError):d=None
    if d is None or timezone.is_naive(d):fail('TIMEZONE_REQUIRED')
    return d
def exact_keys(data,allowed,required=()):
    if not isinstance(data,dict) or set(data)-set(allowed) or not set(required)<=set(data):fail('INVALID_FIELDS')
def get(model,farm,pk):
    try:row=model.objects.filter(exploitation_id=farm,pk=pk).first()
    except (ValueError,TypeError,ModelError):raise NotFound('NOT_FOUND')
    if row is None:raise NotFound('NOT_FOUND')
    return row
def species_for(farm,pk,reproduction=False):
    s=get(Espece,farm,pk)
    if family(s) not in (REPRODUCTION_FAMILIES if reproduction else {'POULTRY','CATTLE','PIG','RABBIT'}):fail('SPECIES_NOT_SUPPORTED')
    return s
def lot_for(farm,pk,reproduction=False,write=False):
    lot=get(Lot,farm,pk);species_for(farm,lot.espece_id,reproduction)
    if write:ensure_open(lot)
    return lot
def public_parameter(p):return {'id':str(p.pk),'species':p.species_id,'code':p.code,'name':p.name,'kind':p.kind,'unit':p.unit,'predefined':p.predefined,'version':p.version}
def public_rule(r):return {'id':str(r.pk),'lot':r.lot_id,'parameter':str(r.parameter_id),'valid_from':r.valid_from.isoformat(),'visible':r.visible,'alerts_enabled':r.alerts_enabled,'lower':str(r.lower) if r.lower is not None else None,'upper':str(r.upper) if r.upper is not None else None,'version':r.version}
def public_reading(r):return {'id':str(r.pk),'lot':r.lot_id,'occurred_at':r.occurred_at.isoformat(),'recorded_at':r.recorded_at.isoformat(),'note':r.note,'values':r.values}
def public_identity(i):return {'id':str(i.pk),'species':i.species_id,'label':i.label}
def public_event(e):return {'id':str(e.pk),'cycle':str(e.cycle_id),'kind':e.kind,'occurred_on':e.occurred_on.isoformat(),'recorded_at':e.recorded_at.isoformat(),'data':e.data,'note':e.note,'movement':e.movement_id}
def public_cycle(c):
    events=list(c.events.order_by('occurred_on','recorded_at','id'));births=[e for e in events if e.kind=='BIRTH'];wean=[e for e in events if e.kind=='WEAN']
    loss=0;known=True
    for e in births:
        if e.movement_id:
            m=e.movement;end=max((x.occurred_on for x in wean if x.occurred_on>=e.occurred_on),default=timezone.localdate())
            if TerrainStockAdjustment.objects.filter(exploitation=c.exploitation,lot=m.lot,kind='ANIMAL').exists() or Mouvement.objects.filter(lot=m.lot,type_mouvement__in=['ACHAT','NAISSANCE']).exclude(pk=m.pk).exists():known=False;continue
            loss+=Mouvement.objects.filter(lot=m.lot,exploitation=c.exploitation,type_mouvement='MORTALITE',date__gte=e.occurred_on,date__lte=end).aggregate(n=Sum('quantite'))['n'] or 0
        elif e.data.get('born_alive',0)>0:known=False
    return {'id':str(c.pk),'lot':c.lot_id,'species':c.lot.espece_id,'identity':str(c.identity_id) if c.identity_id else None,'identity_label':c.identity.label if c.identity_id else None,'mode':c.mode,'name':c.name,'female_count':c.female_count,'started_on':str(c.started_on),'expected_on':str(c.expected_on) if c.expected_on else None,'status':c.status,'version':c.version,'totals':{'born_alive':sum(e.data.get('born_alive',0) for e in births),'stillborn':sum(e.data.get('stillborn',0) for e in births),'weaned':sum(e.data.get('count',0) for e in wean),'preweaning_mortality':loss if known and births else None}}

def apply(user,kind,d):
    farm=user.exploitation_id
    if kind=='setup':
        exact_keys(d,['species'],['species']);s=species_for(farm,d['species']);rows=[]
        for code,name,t,unit in templates(s):
            p,_=TechnicalParameter.objects.get_or_create(exploitation_id=farm,species=s,code=code,defaults={'name':name,'kind':t,'unit':unit,'predefined':True});rows.append(public_parameter(p))
        return {'parameters':rows}
    if kind=='parameter':
        exact_keys(d,['species','name','kind','unit'],['species','name','kind']);s=species_for(farm,d['species'])
        if d['kind'] not in ['NUMBER','TEXT','BOOL']:fail('INVALID_PARAMETER_TYPE')
        p=TechnicalParameter.objects.create(exploitation_id=farm,species=s,code='custom-'+uuid.uuid4().hex,name=label(d['name']),kind=d['kind'],unit=label(d.get('unit',''),30,True));return public_parameter(p)
    if kind=='rule':
        exact_keys(d,['lot','parameter','valid_from','visible','alerts_enabled','lower','upper','expected_version'],['lot','parameter','valid_from','visible','alerts_enabled','expected_version'])
        lot=lot_for(farm,d['lot'],write=True);p=get(TechnicalParameter,farm,d['parameter'])
        if lot.espece_id!=p.species_id:fail('PARAMETER_SPECIES_MISMATCH')
        current=ParameterRule.objects.filter(exploitation_id=farm,lot=lot,parameter=p).order_by('-version').first();version=current.version if current else 0
        if integer(d['expected_version'])!=version:raise Conflict()
        at=instant(d['valid_from'])
        if current and at<=current.valid_from:fail('RULE_DATE_MUST_ADVANCE')
        lower=decimal(d['lower']) if d.get('lower') is not None else None;upper=decimal(d['upper']) if d.get('upper') is not None else None
        if lower is not None and upper is not None and lower>upper:fail('INVALID_BOUNDS')
        enabled=boolean(d['alerts_enabled']);visible=boolean(d['visible'])
        if p.kind!='NUMBER' and (enabled or lower is not None or upper is not None):fail('NUMERIC_RULE_REQUIRED')
        r=ParameterRule.objects.create(exploitation_id=farm,actor=user,lot=lot,parameter=p,valid_from=at,visible=visible,alerts_enabled=enabled,lower=lower,upper=upper,version=version+1,supersedes=current);return public_rule(r)
    if kind=='reading':
        exact_keys(d,['lot','occurred_at','values','note'],['lot','occurred_at','values']);lot=lot_for(farm,d['lot'],write=True);at=instant(d['occurred_at']);items=d['values']
        if not isinstance(items,list) or not 1<=len(items)<=100:fail('VALUES_REQUIRED')
        values=[];seen=set()
        for item in items:
            exact_keys(item,['parameter','value'],['parameter','value']);p=get(TechnicalParameter,farm,item['parameter'])
            if p.species_id!=lot.espece_id or p.pk in seen:fail('INVALID_PARAMETER')
            seen.add(p.pk)
            if item['value'] is None:continue
            v=decimal(item['value']) if p.kind=='NUMBER' else boolean(item['value']) if p.kind=='BOOL' else label(item['value'],1000)
            rule=ParameterRule.objects.filter(exploitation_id=farm,lot=lot,parameter=p,valid_from__lte=at).order_by('-valid_from','-version').first()
            if rule and not rule.visible:fail('PARAMETER_HIDDEN_AT_DATE')
            status='NOT_CONFIGURED';alert=False
            if p.kind=='NUMBER' and rule and rule.alerts_enabled and (rule.lower is not None or rule.upper is not None):
                status='LOW' if rule.lower is not None and v<rule.lower else 'HIGH' if rule.upper is not None and v>rule.upper else 'OK';alert=status!='OK'
            values.append({'parameter':str(p.pk),'name':p.name,'kind':p.kind,'unit':p.unit,'value':str(v) if p.kind=='NUMBER' else v,'rule':public_rule(rule) if rule else None,'status':status,'alert':alert})
        if not values:fail('VALUES_REQUIRED')
        r=TechnicalReading.objects.create(exploitation_id=farm,actor=user,lot=lot,occurred_at=at,values=values,note=label(d.get('note',''),2000,True));return public_reading(r)
    if kind=='identity':
        exact_keys(d,['species','label'],['species','label']);s=species_for(farm,d['species'],True);name=label(d['label'],100)
        i,_=BreederIdentity.objects.get_or_create(exploitation_id=farm,species=s,normalized_label=name.casefold(),defaults={'label':name});return public_identity(i)
    if kind=='cycle':
        exact_keys(d,['lot','mode','identity','name','female_count','started_on','expected_on'],['lot','mode','name','female_count','started_on']);lot=lot_for(farm,d['lot'],True,True)
        if d['mode'] not in ['INDIVIDUAL','COLLECTIVE']:fail('INVALID_MODE')
        identity=get(BreederIdentity,farm,d['identity']) if d.get('identity') else None;count=integer(d['female_count'],1)
        if (d['mode']=='INDIVIDUAL' and (not identity or count!=1)) or (d['mode']=='COLLECTIVE' and identity):fail('INVALID_IDENTITY_MODE')
        start=day(d['started_on']);expected=day(d['expected_on']) if d.get('expected_on') else None
        if expected and expected<start:fail('INVALID_EXPECTED_DATE')
        c=ReproductionCycle.objects.create(exploitation_id=farm,lot=lot,identity=identity,mode=d['mode'],name=label(d['name']),female_count=count,started_on=start,expected_on=expected);return public_cycle(c)
    if kind=='event':
        exact_keys(d,['cycle','kind','occurred_on','data','note','expected_version'],['cycle','kind','occurred_on','data','expected_version']);c=get(ReproductionCycle,farm,d['cycle']);lot_for(farm,c.lot_id,True,True)
        if c.status!='OPEN':raise Conflict('CYCLE_CLOSED')
        if integer(d['expected_version'],1)!=c.version:raise Conflict()
        kind=d['kind'];at=day(d['occurred_on']);data=d['data'].copy() if isinstance(d['data'],dict) else None;movement=None
        if data is None or at<c.started_on:fail('INVALID_EVENT_DATE')
        if kind=='MATING':
            exact_keys(data,['method','male_origin'],['method'])
            if data['method'] not in ['NATURAL','ARTIFICIAL']:fail('INVALID_METHOD')
            data['male_origin']=label(data.get('male_origin',''),200,True)
        elif kind=='GESTATION':
            exact_keys(data,['result','method'],['result'])
            if data['result'] not in ['POSITIVE','NEGATIVE','UNDETERMINED']:fail('INVALID_RESULT')
            data['method']=label(data.get('method',''),200,True)
        elif kind=='NEST':
            if family(c.lot.espece)!='RABBIT':fail('NEST_RABBIT_ONLY')
            exact_keys(data,['state'],['state']);data['state']=label(data['state'],200)
        elif kind=='BIRTH':
            exact_keys(data,['born_alive','stillborn','litters','new_lot_name','production_type','existing_movement'],['born_alive','stillborn'])
            alive=integer(data['born_alive']);still=integer(data['stillborn'])
            if alive+still<1:fail('BIRTH_TOTAL_REQUIRED')
            if c.mode=='COLLECTIVE':integer(data.get('litters'),1)
            elif 'litters' in data and data['litters']!=1:fail('INDIVIDUAL_LITTER_REQUIRED')
            if alive:
                if data.get('existing_movement'):
                    movement=get(Mouvement,farm,data['existing_movement'])
                    if movement.type_mouvement!='NAISSANCE' or movement.lot_origine_id!=c.lot_id or movement.quantite!=alive or movement.mort_nes!=still or movement.date!=at:fail('BIRTH_REFERENCE_MISMATCH')
                    if ReproductionEvent.objects.filter(movement=movement).exists():raise Conflict('BIRTH_ALREADY_LINKED')
                else:
                    from core.serializers import NaissanceCreationSerializer
                    from core.births import create_birth
                    serializer=NaissanceCreationSerializer(data={'total_naissances':alive+still,'mort_nes':still,'nom_nouveau_lot':data.get('new_lot_name'),'type_production':data.get('production_type','CHAIR'),'date':str(at),'note':d.get('note','')});serializer.is_valid(raise_exception=True)
                    child,movement=create_birth(user,c.lot,serializer.validated_data)
                data['offspring_lot']=movement.lot_id
            elif data.get('existing_movement') or data.get('new_lot_name'):fail('NO_LIVING_LOT_FOR_STILLBIRTH')
        elif kind=='WEAN':
            exact_keys(data,['count'],['count']);integer(data['count'])
        elif kind=='CLOSE':
            exact_keys(data,['reason'],['reason']);data['reason']=label(data['reason'],1000);c.status='CLOSED'
        else:fail('INVALID_EVENT_TYPE')
        e=ReproductionEvent.objects.create(exploitation_id=farm,actor=user,cycle=c,kind=kind,occurred_on=at,data=data,note=label(d.get('note',''),2000,True),movement=movement)
        c.version+=1;c.save();return {'event':public_event(e),'cycle':public_cycle(c)}
    fail('UNKNOWN_COMMAND')

@transaction.atomic
def execute(user,kind,body):
    member=active_membership(user)
    if member.role!='OWNER':raise PermissionDenied('OWNER_REQUIRED')
    if not isinstance(body,dict):fail('INVALID_FIELDS')
    exact_keys(body,set(body),['schema_version','operation_uuid'])
    if type(body['schema_version']) is not int or body['schema_version']!=1:fail('SCHEMA_VERSION_REQUIRED')
    try:identifier=uuid.UUID(str(body['operation_uuid']))
    except (ValueError,TypeError):fail('INVALID_OPERATION_UUID')
    farm=Exploitation.objects.select_for_update().get(pk=member.exploitation_id)
    if active_membership(user).role!='OWNER':raise PermissionDenied('OWNER_REQUIRED')
    digest=hashlib.sha256(json.dumps({'kind':kind,'body':body},sort_keys=True,separators=(',',':')).encode()).hexdigest()
    prior=Command.objects.filter(exploitation=farm,operation_uuid=identifier).first()
    if prior:
        if prior.actor_id!=user.pk or prior.fingerprint!=digest:raise Conflict('OPERATION_UUID_REUSED')
        return prior.response,True
    args={k:v for k,v in body.items() if k not in ['schema_version','operation_uuid']}
    with audit_scope(ActorContext(user.pk,farm.pk,owner_http=True)):
        result=apply(user,kind,args)
        Command.objects.create(exploitation=farm,actor=user,operation_uuid=identifier,kind=kind,fingerprint=digest,response=result)
        farm.business_revision+=1;farm.save(update_fields=['business_revision'])
    return result,False


def lot_has_history(lot):
    """Protect links before legacy cascade deletion, with an ordinary API conflict."""
    return (TechnicalReading.objects.filter(lot=lot).exists()
        or ParameterRule.objects.filter(lot=lot).exists()
        or ReproductionCycle.objects.filter(lot=lot).exists()
        or ReproductionEvent.objects.filter(movement__lot=lot).exists())
