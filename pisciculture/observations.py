"""Phase 2A: no stock, loss, finance, food or offline command mutation."""
from decimal import Decimal, localcontext
from fractions import Fraction
from django.db.models import Sum, Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from .models import Observation, ObservationRule, Position, CountLine, Cohort, Installation, Stage
from .services import get, public, fail
from .observation_inputs import PARAMETERS

def exact(value):
    ratio=Fraction(value)
    with localcontext() as ctx:
        ctx.prec=60
        decimal=format(Decimal(ratio.numerator)/Decimal(ratio.denominator),'f')
    return {'decimal':decimal,'numerator':str(ratio.numerator),'denominator':str(ratio.denominator)}

def metadata_at(obj,at):
    from core.models import AuditEvent
    fields=('stage_id','species_id') if isinstance(obj,Cohort) else ('system_code',)
    values={key:getattr(obj,key) for key in fields}
    sources=[]
    for audit in AuditEvent.objects.filter(exploitation_id=obj.exploitation_id,
            entity_type=obj._meta.label_lower,entity_id=str(obj.pk),action='UPDATE',
            applied_at__gt=at).order_by('-applied_at','-pk'):
        for key in fields:
            if key in audit.before_data:values[key]=audit.before_data[key]
        sources.append(audit.pk)
    return values,sources

def count_context(farm, at, *, cohort=None, installation=None):
    positions=Position.objects.filter(exploitation_id=farm)
    if cohort:positions=positions.filter(cohort=cohort)
    if installation:positions=positions.filter(installation=installation)
    rows=[]; gross=0; uncertain=False
    for p in positions.select_related('cohort'):
        lines=CountLine.objects.filter(position=p,event__occurred_at__lte=at)
        n=lines.aggregate(n=Sum('delta_count'))['n'] or 0
        if n<=0:continue
        gross+=n; uncertain |= bool(p.reserved_count)
        metadata,audits=metadata_at(p.cohort,at)
        rows.append({'position':str(p.pk),'cohort':str(p.cohort_id),
            'installation':str(p.installation_id),'species':metadata['species_id'],
            'stage':str(metadata['stage_id']) if metadata['stage_id'] else None,'metadata_audits':audits,
            'gross_count':n,'current_reserved_count':p.reserved_count,
            'source_events':list(lines.order_by('event__occurred_at','event_id').values_list('event__uuid',flat=True))})
    # The current reserve is conservative; never reconstruct historical losses from it.
    for row in rows:row['source_events']=[str(x) for x in row['source_events']]
    return {'at':at.isoformat(),'gross_count':gross,'quality':'UNRECONCILED' if uncertain else 'EXACT',
        'positions':rows,'source':'COUNT_LINE_LEDGER','reserve_context':'CURRENT_CONSERVATIVE',
        'metadata_context':'AUDIT_RECONSTRUCTION_AT_REFERENCE_TIME'}

def applicable(farm,parameter,at,installation=None,species=None,stage=None):
    rules=ObservationRule.objects.filter(exploitation_id=farm,parameter=parameter,valid_from__lte=at).filter(Q(valid_to__isnull=True)|Q(valid_to__gt=at))
    rules=rules.filter(Q(successor__isnull=True)|Q(successor__valid_from__gt=at))
    candidates=[]
    if installation:candidates.append(rules.filter(scope='INSTALLATION',installation=installation))
    if species and stage:candidates.append(rules.filter(scope='SPECIES_STAGE',species_id=species,stage_id=stage))
    if installation and installation.system_code:candidates.append(rules.filter(scope='SYSTEM',system_code=installation.system_code))
    for qs in candidates:
        found=list(qs[:2])
        if len(found)>1:return None,'CONFIG_CONFLICT'
        if found:return found[0],None
    return None,'UNCONFIGURED'

def create_rule(user,args,identifier):
    farm=user.exploitation_id
    for key,model in [('installation',Installation),('cohort',Cohort),('stage',Stage)]:
        if key in args:args[key]=get(model,farm,args[key])
    if args.get('species'):
        from core.models import Espece
        args['species']=get(Espece,farm,args['species'])
        if args['stage'].species_id!=args['species'].pk:raise ValidationError('STAGE_SPECIES_MISMATCH')
    key={k:args.get(k, '' if k=='system_code' else None) for k in ('installation','species','stage','system_code')}
    existing=ObservationRule.objects.filter(exploitation_id=farm,parameter=args['parameter'],scope=args['scope'],**key)
    replacement=args.pop('supersedes',None)
    previous=None
    if replacement:
        previous=get(ObservationRule,farm,replacement)
        if not existing.filter(pk=previous.pk).exists():raise ValidationError('REPLACEMENT_TARGET_MISMATCH')
        if previous.valid_from>=args['valid_from']:raise ValidationError('REPLACEMENT_MUST_BE_LATER')
        if ObservationRule.objects.filter(supersedes=previous).exists():fail('RULE_ALREADY_REPLACED')
        if previous.valid_to and previous.valid_to<args['valid_from']:raise ValidationError('REPLACEMENT_AFTER_END')
    overlap=existing.filter(Q(valid_to__isnull=True)|Q(valid_to__gt=args['valid_from']))
    overlap=overlap.filter(Q(successor__isnull=True)|Q(successor__valid_from__gt=args['valid_from']))
    if previous:overlap=overlap.exclude(pk=previous.pk)
    if args.get('valid_to'):overlap=overlap.filter(valid_from__lt=args['valid_to'])
    if overlap.exists():fail('RULE_INTERVAL_OVERLAP')
    obj=ObservationRule(exploitation_id=farm,actor=user,operation_uuid=identifier,version=existing.count()+1,supersedes=previous,**args)
    obj.full_clean();obj.save()
    return {**public(obj),'unit':PARAMETERS[obj.parameter]}

def create_weight(user,args,identifier):
    ref_at=args.pop('count_reference_at')
    cohort=get(Cohort,user.exploitation_id,args.pop('cohort')) if 'cohort' in args else None
    installation=get(Installation,user.exploitation_id,args.pop('installation')) if 'installation' in args else None
    at=args['occurred_at']
    context=count_context(user.exploitation_id,ref_at,cohort=cohort,installation=installation)
    if context['gross_count']<=0:raise ValidationError('NO_REFERENCE_POPULATION')
    if args.get('sample_count',0)>context['gross_count']:raise ValidationError('SAMPLE_EXCEEDS_REFERENCE')
    mean=Fraction(args['direct_mean_g']) if 'direct_mean_g' in args else Fraction(args['mass_kg'])*1000/args['sample_count']
    biomass=mean*context['gross_count']/1000
    result={'method':'DIRECT_MEAN' if 'direct_mean_g' in args else 'SAMPLE',
        'mean_g':exact(mean),'biomass_kg':exact(biomass) if context['quality']=='EXACT' else None,
        'gross_biomass_estimate_kg':exact(biomass),'biomass_quality':'ESTIMATED' if context['quality']=='EXACT' else 'UNRECONCILED',
        'assumption':'Sample represents the selected population; count is a dated ledger snapshot',
        'count_reference':context,'count_reference_age_seconds':int((at-ref_at).total_seconds()),
        'reference_revision':__import__('core.models',fromlist=['Exploitation']).Exploitation.objects.get(pk=user.exploitation_id).business_revision,'calculated_at':timezone.now().isoformat()}
    metadata,_=metadata_at(cohort,at) if cohort else ({},[])
    if installation:
        installation.system_code=metadata_at(installation,at)[0]['system_code']
    policy,status=applicable(user.exploitation_id,'weight_age_days',at,installation,
        metadata.get('species_id'),metadata.get('stage_id'))
    result['freshness_policy']=public(policy) if policy else None
    result['freshness_policy_status']=status
    obj=Observation(exploitation_id=user.exploitation_id,actor=user,operation_uuid=identifier,kind='WEIGHT',
        cohort=cohort,installation=installation,result=result,**args)
    obj.full_clean();obj.save()
    return serialize(obj)

def create_water(user,args,identifier):
    installation=get(Installation,user.exploitation_id,args.pop('installation'))
    context=count_context(user.exploitation_id,args['occurred_at'],installation=installation)
    installation.system_code=metadata_at(installation,args['occurred_at'])[0]['system_code']
    populations={r['cohort']:r for r in context['positions']}
    evaluations=[]
    for parameter in ('temperature_c','ph','oxygen_mg_l'):
        value=args.get(parameter)
        if value is None:continue
        for population in list(populations.values()) or [None]:
            rule,status=applicable(user.exploitation_id,parameter,args['occurred_at'],installation,
                population['species'] if population else None,population['stage'] if population else None)
            if rule:
                status='LOW' if rule.lower is not None and value<rule.lower else 'HIGH' if rule.upper is not None and value>rule.upper else 'OK'
            evaluations.append({'parameter':parameter,'value':str(value),'unit':PARAMETERS[parameter],
                'cohort':population['cohort'] if population else None,'status':status,
                'rule':public(rule) if rule else None,'alert':bool(rule and status in ('LOW','HIGH'))})
    conflicts=[]
    for parameter in ('temperature_c','ph','oxygen_mg_l'):
        selected=[e['rule'] for e in evaluations if e['parameter']==parameter and e['rule']]
        lower=[Decimal(r['lower']) for r in selected if r['lower'] is not None]
        upper=[Decimal(r['upper']) for r in selected if r['upper'] is not None]
        if lower and upper and max(lower)>min(upper):conflicts.append(parameter)
    result={'evaluations':evaluations,'incompatible_population_thresholds':conflicts,
        'population_context':context,'engine_version':1,'has_alert':any(e['alert'] for e in evaluations),
        'units':{p:PARAMETERS[p] for p in ('temperature_c','ph','oxygen_mg_l')}}
    obj=Observation(exploitation_id=user.exploitation_id,actor=user,operation_uuid=identifier,
        kind='WATER',installation=installation,result=result,**args)
    obj.full_clean();obj.save()
    return serialize(obj)

def serialize(obj):
    data=public(obj)
    if obj.kind=='WEIGHT':
        # Policy at the measure date is stable; age is computed for display only.
        policy=obj.result['freshness_policy']
        seconds=max(0,int((timezone.now()-obj.occurred_at).total_seconds()))
        data['freshness']={'age_seconds':seconds,'policy':policy,
            'status':obj.result['freshness_policy_status'] or ('STALE' if Decimal(seconds)>Decimal(policy['upper'])*86400 else 'CURRENT')}
    return data
