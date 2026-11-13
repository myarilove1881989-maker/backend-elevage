"""Recorded care only. No medical advice, financial writes or sanitary stock debit."""
import uuid
from django.utils import timezone
from rest_framework.exceptions import ValidationError,PermissionDenied
from core.supply_models import SupplyProduct
from core.models import Exploitation
from .models import HealthRecord,SanitaryProduct,Installation,Cohort,Event,CountLine
from .services import access,get,public,fail
from .stock_ledger import replay
from .observations import metadata_at,count_context

UNKNOWN_WITHDRAWAL='Délai avant commercialisation non renseigné — vérification nécessaire'

def product_public(item):
    supply=isinstance(item,SupplyProduct)
    return {'id':str(item.pk),'source':'SUPPLY' if supply else 'SANITARY','name':item.name,
        'type':'VACCINE' if supply else item.type,'unit':'dose' if supply else item.unit,
        'information':'' if supply else item.information,'archived':item.archived}

def create_product(user,farm,args,identifier):
    if access(user,'aquatic.read').role!='OWNER':raise PermissionDenied('OWNER_REQUIRED')
    name=args['name']
    if SupplyProduct.objects.filter(exploitation=farm,kind='VACCINE',name__iexact=name).exists() or SanitaryProduct.objects.filter(exploitation=farm,name__iexact=name).exists():
        fail('SANITARY_PRODUCT_ALREADY_EXISTS_SELECT_EXISTING')
    item=SanitaryProduct(exploitation=farm,created_by=user,**args);item.full_clean();item.save()
    return product_public(item)

def population(farm,site,cohort,at):
    state=replay(farm,at)
    present=sorted(c for (c,i),n in state['gross'].items() if i==str(site.pk) and n>0 and (cohort is None or c==str(cohort.pk)))
    if not present:raise ValidationError('POPULATION_NOT_PRESENT_AT_HEALTH_DATE')
    species=set();metadata=[]
    for row in Cohort.objects.filter(exploitation_id=farm,pk__in=present):
        values,sources=metadata_at(row,at);species.add(values['species_id'])
        metadata.append({'cohort':str(row.pk),'species':values['species_id'],'audit_sources':sources})
    return present,sorted(species),{'installation':str(site.pk),'cohorts':metadata,
        'count_reference':count_context(farm,at,cohort=cohort,installation=None if cohort else site)}

def create_record(user,farm,args,identifier,kind,previous=None,correction_reason=''):
    data=dict(args)
    site=get(Installation,farm.pk,data.pop('installation'))
    cohort_id=data.pop('cohort',None);cohort=get(Cohort,farm.pk,cohort_id) if cohort_id else None
    at=data['occurred_at'];eligible,species,scope=population(farm.pk,site,cohort,at)
    if previous and HealthRecord.objects.filter(supersedes=previous).exists():fail('HEALTH_RECORD_ALREADY_RECTIFIED')
    if previous and previous.kind!=kind:raise ValidationError('HEALTH_CORRECTION_KIND_MISMATCH')
    product=None;observation=None;until=None
    if kind=='TREATMENT':
        data.pop('administration_status')
        source='supply_product' if 'supply_product' in data else 'sanitary_product'
        product=get(SupplyProduct if source=='supply_product' else SanitaryProduct,farm.pk,data.pop(source))
        if source=='supply_product' and product.kind!='VACCINE':raise ValidationError('SANITARY_PRODUCT_REQUIRED')
        if product.archived and not (previous and getattr(previous,source+'_id')==product.pk):raise ValidationError('SANITARY_PRODUCT_ARCHIVED')
        expected='dose' if source=='supply_product' else product.unit
        if data['unit']!=expected:raise ValidationError('SANITARY_PRODUCT_UNIT_MISMATCH')
        data[source]=product
        oid=data.pop('observation',None)
        if oid:
            observation=get(HealthRecord,farm.pk,oid,kind='OBSERVATION')
            if observation.installation_id!=site.pk or (observation.cohort_id and cohort and observation.cohort_id!=cohort.pk) or observation.occurred_at>at:
                raise ValidationError('HEALTH_OBSERVATION_SCOPE_OR_DATE_MISMATCH')
        if data['withdrawal_days'] is not None:
            until=at+timezone.timedelta(days=data['withdrawal_days']+(1 if data['time_precision']=='DATE' else 0))
    identifier_pk=uuid.uuid4()
    fact=HealthRecord(id=identifier_pk,exploitation=farm,actor=user,command_uuid=identifier,kind=kind,
        installation=site,cohort=cohort,eligible_cohorts=eligible,species_ids=species,scope_snapshot=scope,
        product_snapshot=product_public(product) if product else {},observation=observation,withdrawal_until=until,
        root_id=previous.root_id if previous else identifier_pk,supersedes=previous,correction_reason=correction_reason,**data)
    fact.full_clean();fact.save()
    return record_public(fact)

def apply(user,farm,args,identifier,kind):
    if kind=='health_product':return create_product(user,farm,args,identifier)
    if kind=='health_correction':
        if access(user,'aquatic.read').role!='OWNER':raise PermissionDenied('OWNER_REQUIRED')
        previous=get(HealthRecord,farm.pk,args['target'])
        replacement='observation' if previous.kind=='OBSERVATION' else 'treatment'
        if replacement not in args:raise ValidationError('HEALTH_CORRECTION_KIND_MISMATCH')
        return create_record(user,farm,args[replacement],identifier,previous.kind,previous,args['reason'])
    return create_record(user,farm,args,identifier,'OBSERVATION' if kind=='health_observation' else 'TREATMENT')

def precaution(fact,at):
    if fact.withdrawal_days is None:return {'status':'UNKNOWN','message':UNKNOWN_WITHDRAWAL,'until':None}
    status='ACTIVE' if at<fact.withdrawal_until else 'ELAPSED_RECORDED'
    return {'status':status,'message':'Délai documenté en cours' if status=='ACTIVE' else 'Délai documenté écoulé — vérification nécessaire',
        'until':fact.withdrawal_until.isoformat(),'days':fact.withdrawal_days,'source':fact.withdrawal_source,
        'basis':'END_OF_DAY_CONSERVATIVE' if fact.time_precision=='DATE' else 'ELAPSED_24H_DAYS',
        'end_date':(fact.business_date+timezone.timedelta(days=fact.withdrawal_days)).isoformat() if fact.business_date else None}

def record_public(fact):
    data=public(fact)
    data.update(occurred_at=fact.occurred_at.isoformat(),recorded_at=fact.recorded_at.isoformat(),withdrawal_until=fact.withdrawal_until.isoformat() if fact.withdrawal_until else None)
    successor=HealthRecord.objects.filter(supersedes_id=fact.pk).values_list('pk',flat=True).first()
    data.update(actor_name=fact.actor.get_full_name() or fact.actor.username,is_current=successor is None,
        superseded_by=str(successor) if successor else None,administration_status='ADMINISTERED' if fact.kind=='TREATMENT' else None,
        stock_automated=False,expense_created=False,photo=None)
    if fact.kind=='TREATMENT':data['precaution']=precaution(fact,timezone.now())
    return data

def current(farm):
    return HealthRecord.objects.filter(exploitation_id=farm,kind='TREATMENT',successor__isnull=True).select_related('actor')

def sale_check(user,site,cohort,at):
    """Evidence for future 3C. Never authorizes a sale or propagates a restriction blindly."""
    state=replay(user.exploitation_id,at)
    candidates={c for (c,i),n in state['gross'].items() if i==str(site.pk) and n>0 and (cohort is None or c==str(cohort.pk))}
    if cohort and str(cohort.pk) not in candidates:raise ValidationError('COHORT_NOT_PRESENT_AT_CHECK_DATE')
    matches=[]
    for record in current(user.exploitation_id).filter(occurred_at__lte=at):
        historical=replay(user.exploitation_id,record.occurred_at)
        now_eligible=sorted(c for (c,i),n in historical['gross'].items() if i==str(record.installation_id) and n>0 and (record.cohort_id is None or c==str(record.cohort_id)))
        historical_changed=now_eligible!=sorted(record.eligible_cohorts)
        if historical_changed and (site.pk==record.installation_id or candidates & (set(record.eligible_cohorts)|set(now_eligible))):
            matches.append({'record':str(record.pk),'root_id':str(record.root_id),'candidate_cohort':None,
                'recorded_installation':str(record.installation_id),'scope_match':'HISTORICAL_CONTEXT_CHANGED',
                'ongoing':record.ongoing,'precaution':precaution(record,at)})
        exposed=candidates & set(record.eligible_cohorts)
        for candidate in sorted(exposed):
            events=Event.objects.filter(exploitation_id=user.exploitation_id,cohort_id=candidate,
                type='TRANSFER',occurred_at__gte=record.occurred_at,occurred_at__lte=at).order_by('occurred_at','recorded_at','pk')
            reachable={str(record.installation_id)};moved=False
            for event in events:
                lines=list(CountLine.objects.filter(event=event).select_related('position'))
                sources={str(x.position.installation_id) for x in lines if x.delta_count<0}
                if reachable & sources:
                    reachable.update(str(x.position.installation_id) for x in lines if x.delta_count>0);moved=True
            if str(site.pk) not in reachable:continue
            matches.append({'record':str(record.pk),'root_id':str(record.root_id),'candidate_cohort':candidate,
                'recorded_installation':str(record.installation_id),'scope_match':'TRACEABILITY_REQUIRED' if moved else 'RECORDED_SCOPE',
                'ongoing':record.ongoing,'precaution':precaution(record,at)})
    status='NO_RECORDED_TREATMENT'
    if matches:
        status='DOCUMENTED_PERIOD_ELAPSED'
        if any(m['precaution']['status']=='ACTIVE' for m in matches):status='RESTRICTION_RECORDED'
        if any(m['precaution']['status']=='UNKNOWN' for m in matches):status='UNKNOWN_WITHDRAWAL'
        if any(m['ongoing'] for m in matches):status='ONGOING_REVIEW'
        if any(m['scope_match'] in ('TRACEABILITY_REQUIRED','HISTORICAL_CONTEXT_CHANGED') for m in matches):status='TRACEABILITY_REVIEW'
    return {'contract_version':1,'installation':str(site.pk),'cohort':str(cohort.pk) if cohort else None,
        'as_of':at.isoformat(),'business_revision':Exploitation.objects.values_list('business_revision',flat=True).get(pk=user.exploitation_id),'status':status,
        'matches':matches,'sale_authorized':None,'verification_required':True,
        'notice':'Contrat préparatoire 3C : aucun résultat ne constitue une autorisation de vente.'}
