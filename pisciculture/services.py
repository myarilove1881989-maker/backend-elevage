import hashlib
import json
import uuid
from datetime import datetime
from decimal import Decimal
from django.db import transaction
from django.db.models import Sum, Max
from django.core.serializers.json import DjangoJSONEncoder
from django.utils import timezone
from rest_framework.exceptions import APIException, NotFound, PermissionDenied, ValidationError
from core.models import Exploitation, Espece
from core.access_policy import active_membership, authorize
from core.audit import audit_scope, ActorContext
from .models import Activity, Installation, AquaticSpeciesProfile, Stage, Cohort, Position, Command, Event, CountLine


class Conflict(APIException):
    status_code = 409
    default_code = 'CONFLICT'


def fail(code):
    raise Conflict(code)


def get(model, farm, pk, **extra):
    try:
        return model.objects.get(pk=pk,exploitation_id=farm,**extra)
    except (model.DoesNotExist, ValueError):
        raise NotFound('NOT_FOUND')


def enabled(farm):
    return Activity.objects.filter(exploitation_id=farm,enabled=True).exists()


def access(user, action, *, activation=False):
    member = active_membership(user)
    if activation:
        if member.role != 'OWNER':
            raise PermissionDenied('OWNER_REQUIRED')
    else:
        if not enabled(user.exploitation_id):
            raise PermissionDenied('MODULE_DISABLED')
        authorize(user,user.exploitation_id,action,owner_http=member.role=='OWNER')
    return member


def public(obj):
    data = {f.name: getattr(obj,f.attname) for f in obj._meta.concrete_fields
            if f.name not in {'exploitation'}}
    if isinstance(obj,Position):
        data['available_count'] = obj.gross_count - obj.reserved_count
        data['count_quality'] = 'UNRECONCILED' if obj.reserved_count else 'EXACT'
    if isinstance(obj,Cohort):
        data['species_name']=obj.species.nom
        data['stage_name']=obj.stage.name if obj.stage_id else None
        positions = sorted(obj.positions.all(),key=lambda p:str(p.pk))
        data['positions'] = [public(p) for p in positions]
        data['gross_count'] = sum(p.gross_count for p in positions)
        data['count_quality'] = 'UNRECONCILED' if any(p.reserved_count for p in positions) else 'EXACT'
    return json.loads(json.dumps(data,cls=DjangoJSONEncoder))


def etag(obj):
    return f'"{obj.pk}:{obj.version}"'


def event(actor, command_id, cohort, kind, at, reason, lines):
    ev = Event.objects.create(exploitation_id=actor.exploitation_id,command_uuid=command_id,
        cohort=cohort,type=kind,occurred_at=at,actor=actor,reason=reason)
    for index,(position,delta) in enumerate(lines):
        CountLine.objects.create(event=ev,position=position,delta_count=delta,line_no=index)
    return ev


def check_timeline(cohort, at, proposed):
    rows = [(line.event.occurred_at,line.event_id,line.line_no,line.position_id,line.delta_count)
        for line in CountLine.objects.filter(position__cohort=cohort).select_related('event')]
    sequence = max((r[1] for r in rows),default=0)+1
    rows += [(at,sequence,i,p.pk,delta) for i,(p,delta) in enumerate(proposed)]
    balances = {}
    for _,_,_,pid,delta in sorted(rows):
        balances[pid] = balances.get(pid,0)+delta
        if balances[pid]<0:
            fail('TIMELINE_CONFLICT')


def metadata_version(obj, match):
    if not match:
        error = APIException('PRECONDITION_REQUIRED');error.status_code=428;raise error
    if match != etag(obj):
        error = APIException('PRECONDITION_FAILED');error.status_code=412;raise error


@transaction.atomic
def execute(user, kind, data, *, pk=None, match=None, device=None):
    activation = kind=='activation'
    actions = {'installation':'aquatic.installations.manage','installation_patch':'aquatic.installations.manage',
        'species':'aquatic.cohorts.manage','stage':'aquatic.cohorts.manage','cohort':'aquatic.cohorts.manage',
        'cohort_patch':'aquatic.cohorts.manage','transfer':'aquatic.transfers.create'}
    actions['onboarding']='aquatic.cohorts.manage'
    actions.update(brood_group='aquatic.breeders.manage',brood_group_patch='aquatic.read',reproduction='aquatic.reproduction.create',reproduction_patch='aquatic.read',breeding_step='aquatic.reproduction.create',hatch_count='aquatic.fry.stock',fry_placement='aquatic.fry.stock',breeding_void='aquatic.read')
    actions.update(health_observation='aquatic.health.observe',health_treatment='aquatic.health.treat',health_product='aquatic.read',health_correction='aquatic.read')
    actions.update(feed_purchase='aquatic.feed.purchase',feed_distribution='aquatic.feed.distribute',feed_reserve='aquatic.read')
    actions.update(mortality='aquatic.losses.create',reconcile='aquatic.read',inventory='aquatic.read',assign_credit='aquatic.read',stock_cancel='aquatic.read')
    actions.update(weight='aquatic.weights.create',water='aquatic.water.create',observation_rule='aquatic.water.rules.manage')
    access(user,actions.get(kind,'aquatic.read'),activation=activation)
    if kind in ('reconcile','inventory','assign_credit','stock_cancel','feed_reserve'):
        if active_membership(user).role!='OWNER':raise PermissionDenied('OWNER_REQUIRED')
    farm = Exploitation.objects.select_for_update().get(pk=user.exploitation_id)
    access(user,actions.get(kind,'aquatic.read'),activation=activation)
    if kind=='feed_reserve' and active_membership(user).role!='OWNER':raise PermissionDenied('OWNER_REQUIRED')
    if kind in ('health_product','health_correction') and active_membership(user).role!='OWNER':raise PermissionDenied('OWNER_REQUIRED')
    if kind in ('brood_group_patch','reproduction_patch','breeding_void') and active_membership(user).role!='OWNER':raise PermissionDenied('OWNER_REQUIRED')
    if kind=='fry_placement':access(user,'aquatic.cohorts.manage')
    if kind=='feed_purchase':
        access(user,'expenses.create')
        if ('new_product' in data or 'new_category' in data) and active_membership(user).role!='OWNER':
            raise PermissionDenied('OWNER_REQUIRED_FOR_CATALOG')
    identifier = data['operation_uuid']
    payload = {'kind':kind,'pk':str(pk) if pk else None,'match':match,'data':data}
    digest = hashlib.sha256(json.dumps(payload,sort_keys=True,cls=DjangoJSONEncoder,separators=(',',':')).encode()).hexdigest()
    prior = Command.objects.filter(exploitation=farm,operation_uuid=identifier).first()
    if prior:
        if prior.actor_id!=user.pk:
            raise NotFound('NOT_FOUND')
        if prior.fingerprint!=digest:
            fail('UUID_PAYLOAD_MISMATCH')
        return prior.result,prior.response_status,True
    args = {key:value for key,value in data.items() if key not in {'operation_uuid','schema_version'}}
    with audit_scope(ActorContext(user.pk,farm.pk,device_id=device.pk if device else None,operation_id=identifier,
            owner_http=user.pk==farm.proprietaire_id),reason_text=args.get('reason','')):
        status = 201
        if kind=='onboarding':
            from .serializers import InstallationInput, SpeciesInput, CohortInput
            results={}
            for key,child_kind,cls in [('installation','installation',InstallationInput),('species','species',SpeciesInput),('cohort','cohort',CohortInput)]:
                body=args[key].copy()
                if key=='species' and 'existing_species' in body:
                    if set(body)!={'existing_species'} or type(body['existing_species']) is not int:
                        raise ValidationError('INVALID_EXISTING_SPECIES')
                    species=get(Espece,farm.pk,body['existing_species'])
                    if not AquaticSpeciesProfile.objects.filter(exploitation=farm,species=species,active=True).exists():raise ValidationError('AQUATIC_SPECIES_REQUIRED')
                    results[key]={'species':species.pk};continue
                if key=='cohort':body['species']=results['species']['species']
                body.update(operation_uuid=uuid.uuid5(identifier,key),schema_version=1)
                serializer=cls(data=body);serializer.is_valid(raise_exception=True)
                results[key],_,_=execute(user,child_kind,serializer.validated_data,device=device)
            farm.refresh_from_db()
            result=results
        elif activation:
            activity,_ = Activity.objects.get_or_create(exploitation=farm)
            activity.enabled = args['enabled'];activity.version+=1;activity.save()
            result = public(activity)
        elif kind=='installation':
            if not {'id','code','name','type'}<=args.keys():
                raise ValidationError('INSTALLATION_FIELDS_REQUIRED')
            obj = Installation(exploitation=farm,**args);obj.full_clean();obj.save();result=public(obj)
        elif kind=='installation_patch':
            obj=get(Installation,farm.pk,pk);metadata_version(obj,match)
            if 'id' in args:raise ValidationError('IDENTITY_IMMUTABLE')
            if args.get('status')=='INACTIVE':
                from .stock_ledger import replay,counts
                inventory=counts(replay(farm.pk),str(obj.pk))
                if inventory['pending_count'] or inventory['unassigned_credit']:fail('INSTALLATION_UNRECONCILED')
            if args.get('status')=='INACTIVE' and obj.positions.filter(gross_count__gt=0).exists():
                fail('INSTALLATION_OCCUPIED')
            for key,value in args.items():setattr(obj,key,value)
            obj.version+=1;obj.full_clean();obj.save();result=public(obj);status=200
        elif kind=='species':
            if ('species' in args)==('name' in args):raise ValidationError('CHOOSE_SPECIES_OR_NAME')
            species = get(Espece,farm.pk,args['species']) if 'species' in args else Espece.objects.create(exploitation=farm,nom=args['name'])
            profile=AquaticSpeciesProfile(exploitation=farm,id=args['id'],species=species)
            profile.full_clean();profile.save();result={**public(profile),'name':species.nom}
        elif kind=='stage':
            species=get(Espece,farm.pk,args.pop('species'))
            if not AquaticSpeciesProfile.objects.filter(exploitation=farm,species=species,active=True).exists():raise ValidationError('AQUATIC_SPECIES_REQUIRED')
            obj=Stage(exploitation=farm,species=species,**args);obj.full_clean();obj.save();result=public(obj)
        elif kind=='cohort':
            allocations=args.pop('allocations');initial=args.pop('initial_count');reason=args.pop('reason')
            if len(allocations)>100 or len({a['installation'] for a in allocations})!=len(allocations) or sum(a['count'] for a in allocations)!=initial:
                raise ValidationError('ALLOCATION_TOTAL_INVALID')
            species=get(Espece,farm.pk,args.pop('species'))
            if not AquaticSpeciesProfile.objects.filter(exploitation=farm,species=species,active=True).exists():raise ValidationError('AQUATIC_SPECIES_REQUIRED')
            stage=get(Stage,farm.pk,args.pop('stage')) if args.get('stage') else None
            args.pop('stage',None)
            if stage and stage.species_id!=species.pk:raise ValidationError('STAGE_SPECIES_MISMATCH')
            cohort=Cohort(exploitation=farm,species=species,stage=stage,**args);cohort.full_clean();cohort.save()
            lines=[]
            for a in allocations:
                installation=get(Installation,farm.pk,a['installation'],status='ACTIVE')
                position=Position.objects.create(exploitation=farm,cohort=cohort,installation=installation,gross_count=a['count'])
                lines.append((position,a['count']))
            ev=event(user,identifier,cohort,'INITIAL',cohort.started_at,reason,lines)
            from .models import StockFact
            if StockFact.objects.filter(exploitation=farm).exists():
                from .stock_ledger import sync
                sync(farm.pk)
            result={**public(cohort),'event_id':str(ev.uuid),'initial_count':initial}
        elif kind=='cohort_patch':
            cohort=get(Cohort,farm.pk,pk);metadata_version(cohort,match)
            if args.get('status')=='CLOSED':
                from .stock_ledger import cohort_has_pending
                if cohort.positions.filter(gross_count__gt=0).exists() or cohort_has_pending(farm.pk,cohort.pk):fail('COHORT_OCCUPIED_OR_UNRECONCILED')
            if 'stage' in args and args['stage']:
                stage=get(Stage,farm.pk,args['stage'])
                if stage.species_id!=cohort.species_id:raise ValidationError('STAGE_SPECIES_MISMATCH')
                args['stage']=stage
            for key,value in args.items():setattr(cohort,key,value)
            cohort.version+=1;cohort.full_clean();cohort.save();result=public(cohort);status=200
        elif kind=='transfer':
            cohort=get(Cohort,farm.pk,args['cohort'],status='OPEN')
            if args['source']==args['destination']:raise ValidationError('SAME_INSTALLATION')
            source=get(Installation,farm.pk,args['source'],status='ACTIVE')
            target=get(Installation,farm.pk,args['destination'],status='ACTIVE')
            positions=list(Position.objects.select_for_update().filter(cohort=cohort,installation__in=[source,target]).order_by('id'))
            src=next((p for p in positions if p.installation_id==source.pk),None)
            dst=next((p for p in positions if p.installation_id==target.pk),None)
            versions=args['expected_versions']
            if not src:raise NotFound('SOURCE_POSITION_REQUIRED')
            if (cohort.version,src.version,dst.version if dst else 0)!=(versions['cohort'],versions['source_position'],versions['destination_position']):fail('STALE_RESOURCE')
            quantity=args['count']
            if quantity>src.gross_count:fail('INSUFFICIENT_STOCK')
            if quantity>src.gross_count-src.reserved_count:fail('UNRECONCILED_RESERVE')
            if not dst:dst=Position.objects.create(exploitation=farm,cohort=cohort,installation=target)
            # Full replay below includes outstanding losses, credits and all later prefixes.
            from .models import StockFact
            if not StockFact.objects.filter(exploitation=farm).exists():
                check_timeline(cohort,args['occurred_at'],[(src,-quantity),(dst,quantity)])
            total=cohort.positions.aggregate(n=Sum('gross_count'))['n'] or 0
            src.gross_count-=quantity;dst.gross_count+=quantity
            for p in [src,dst]:p.version+=1;p.save()
            cohort.version+=1;cohort.save()
            ev=event(user,identifier,cohort,'TRANSFER',args['occurred_at'],args['reason'],[(src,-quantity),(dst,quantity)])
            if StockFact.objects.filter(exploitation=farm).exists():
                from .stock_ledger import sync
                sync(farm.pk)
                src.refresh_from_db();dst.refresh_from_db();cohort.refresh_from_db()
            result={'event_id':str(ev.uuid),'positions':[public(src),public(dst)],'cohort_version':cohort.version,
                'cohort_count_before':total,'cohort_count_after':total,'command_status':'APPLIED'}
        elif kind in ('mortality','reconcile','inventory','assign_credit','stock_cancel'):
            from .stock_ledger import apply
            result=apply(user,kind,args,identifier,farm)
        elif kind in ('brood_group','brood_group_patch','reproduction','reproduction_patch','breeding_step','hatch_count','fry_placement','breeding_void'):
            from .breeding_services import apply
            result=apply(user,farm,args,identifier,kind,device)
            farm.refresh_from_db()
        elif kind in ('health_observation','health_treatment','health_correction','health_product'):
            from .health_services import apply
            result=apply(user,farm,args,identifier,kind)
        elif kind in ('feed_purchase','feed_distribution','feed_reserve'):
            from .feeding import purchase,distribute,reserve
            result={'feed_purchase':purchase,'feed_distribution':distribute,'feed_reserve':reserve}[kind](user,farm,args,identifier)
        elif kind in ('weight','water','observation_rule'):
            from .observations import create_weight, create_water, create_rule
            result={'weight':create_weight,'water':create_water,'observation_rule':create_rule}[kind](user,args,identifier)
        else:raise ValidationError('UNKNOWN_COMMAND')
        farm.business_revision+=1;farm.save(update_fields=['business_revision'])
        result['business_revision']=farm.business_revision
        Command.objects.create(exploitation=farm,operation_uuid=identifier,kind=kind,fingerprint=digest,
            actor=user,result=result,response_status=status)
    return result,status,False
