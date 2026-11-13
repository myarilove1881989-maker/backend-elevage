"""Pre-stocking reproduction facts; the existing cohort journal remains the only live stock."""
import uuid
from django.utils import timezone
from rest_framework.exceptions import ValidationError,PermissionDenied
from core.access_policy import active_membership
from core.models import Espece
from .models import BroodGroup,Reproduction,BreedingFact,Installation,Cohort,AquaticSpeciesProfile,Position,Event,StockFact,Observation,FeedDistribution,HealthRecord
from .services import get,fail,public,event
from .stock_ledger import replay as stock_replay,sync,cohort_has_pending
from .observations import metadata_at


def brood_reference(farm,species,site,cohort,at,males,females):
    if cohort is None:return {'quality':'UNLINKED','notice':'Groupe descriptif, aucune entrée ni réservation de poissons.'}
    metadata,_=metadata_at(cohort,at)
    if metadata['species_id']!=species.pk:raise ValidationError('BROOD_SPECIES_MISMATCH')
    state=stock_replay(farm,at);count=state['gross'].get((str(cohort.pk),str(site.pk)),0)
    if count<=0:raise ValidationError('BROOD_COHORT_NOT_PRESENT_AT_DATE')
    if sum(n for n in [males,females] if n is not None)>count:raise ValidationError('BROOD_SEX_COUNTS_EXCEED_REFERENCE')
    return {'quality':'LINKED_DECLARATION','cohort':str(cohort.pk),'installation':str(site.pk),'reference_at':at.isoformat(),
        'gross_count':count,'source_events':state['source_events'],'notice':'Référence descriptive, aucun second effectif ni réserve de géniteurs.'}


def group_public(row):
    result=public(row);result['species_name']=row.species.nom;result['installation_name']=row.installation.name
    result['occurred_at']=row.occurred_at.isoformat();return result


def reproduce_public(row):
    return {**public(row),'group_name':row.group.name,'species_name':row.species.nom,
        'installation_name':row.installation.name,'occurred_at':row.occurred_at.isoformat(),
        'fertilization_rate':None,'hatching_rate':None}


def timeline(reproduction,at=None):
    qs=BreedingFact.objects.filter(reproduction=reproduction).select_related('parent')
    if at:qs=qs.filter(occurred_at__lte=at)
    seen={};voided=set();hatches={}
    for row in qs.order_by('occurred_at','recorded_at','pk'):
        key=str(row.pk);parent=str(row.parent_id) if row.parent_id else None
        if row.occurred_at<reproduction.occurred_at:fail('BREEDING_TIMELINE_CONFLICT')
        if parent and (parent not in seen or parent in voided):fail('BREEDING_PARENT_NOT_ACTIVE_AT_DATE')
        if row.kind=='HATCH':
            hatches[key]={'confirmed_total':row.quantity if row.quality=='COUNTED' else None,'placed_total':0,'counts':[],
                'initial_confirmed':row.quantity if row.quality=='COUNTED' else None,'voided':False}
        elif row.kind in ('COUNT','PLACEMENT'):
            if parent not in hatches:fail('HATCH_REFERENCE_REQUIRED')
            state=hatches[parent]
            if row.kind=='COUNT':state['counts'].append(row);state['confirmed_total']=row.quantity
            else:
                if state['confirmed_total'] is None:fail('HATCH_COUNT_CONFIRMATION_REQUIRED')
                state['placed_total']+=row.quantity
            if state['confirmed_total'] is not None and state['placed_total']>state['confirmed_total']:fail('HATCH_CONFIRMED_QUANTITY_EXCEEDED')
        elif row.kind=='VOID':
            target=seen[parent]
            children=[x for identifier,x in seen.items() if x.parent_id==target.pk and identifier not in voided and x.kind!='VOID']
            if children:fail('BREEDING_DEPENDENTS_MUST_BE_VOIDED_FIRST')
            if target.kind=='COUNT':
                state=hatches[str(target.parent_id)]
                state['counts']=[x for x in state['counts'] if x.pk!=target.pk]
                state['confirmed_total']=state['counts'][-1].quantity if state['counts'] else state['initial_confirmed']
                if state['placed_total'] and (state['confirmed_total'] is None or state['placed_total']>state['confirmed_total']):fail('HATCH_CONFIRMED_QUANTITY_EXCEEDED')
            elif target.kind=='PLACEMENT':hatches[str(target.parent_id)]['placed_total']-=target.quantity
            elif target.kind=='HATCH':hatches[parent]['voided']=True
            voided.add(parent)
        seen[key]=row
    for state in hatches.values():
        state['available_confirmed']=None if state['confirmed_total'] is None else state['confirmed_total']-state['placed_total']
        state['confirmation_status']='UNKNOWN' if state['confirmed_total'] is None else 'COUNTED'
        state.pop('counts');state.pop('initial_confirmed')
    return {'hatches':hatches,'voided':voided,'facts':seen}


def fact_public(row,state=None):
    state=state or timeline(row.reproduction)
    data=public(row);data.update(occurred_at=row.occurred_at.isoformat(),actor_name=row.actor.get_full_name() or row.actor.username,
        canceled=str(row.pk) in state['voided'],species=row.reproduction.species_id,group=str(row.reproduction.group_id),
        species_name=row.reproduction.species.nom,group_name=row.reproduction.group.name,installation_name=row.installation.name)
    if row.kind=='HATCH':data['availability']=state['hatches'][str(row.pk)]
    return data


def create_fact(user,farm,identifier,rep,kind,site,at,*,parent=None,quantity=None,quality='UNKNOWN',cohort=None,stock_event=None,
                reason='',note='',payload=None,time_precision='DATETIME',business_date=None):
    row=BreedingFact(exploitation=farm,reproduction=rep,actor=user,command_uuid=identifier,kind=kind,installation=site,
        occurred_at=at,parent=parent,quantity=quantity,quality=quality,cohort=cohort,stock_event=stock_event,reason=reason,
        note=note,payload=payload or {},time_precision=time_precision,business_date=business_date)
    row.full_clean();row.save();return row


def hatch_for(farm,args):
    hatch=get(BreedingFact,farm,args['hatch'],kind='HATCH')
    rep=hatch.reproduction
    if rep.status!='OPEN':fail('REPRODUCTION_CLOSED')
    return hatch,rep


def placement_dependencies(cohort,original):
    site=original.installation_id;at=original.occurred_at
    return (Event.objects.filter(cohort=cohort).exclude(pk=original.stock_event_id).exists()
        or cohort_has_pending(cohort.exploitation_id,cohort.pk)
        or StockFact.objects.filter(exploitation_id=cohort.exploitation_id).filter(installation_id=site,occurred_at__gte=at).exists()
        or Observation.objects.filter(exploitation_id=cohort.exploitation_id,occurred_at__gte=at).filter(installation_id=site).exists()
        or Observation.objects.filter(cohort=cohort).exists()
        or FeedDistribution.objects.filter(exploitation_id=cohort.exploitation_id,installation_id=site,occurred_at__gte=at).exists()
        or HealthRecord.objects.filter(exploitation_id=cohort.exploitation_id,eligible_cohorts__contains=[str(cohort.pk)]).exists()
        or BroodGroup.objects.filter(cohort=cohort).exists())


def apply(user,farm,args,identifier,kind,device=None):
    data=dict(args)
    if kind=='brood_group':
        species=get(Espece,farm.pk,data.pop('species'));site=get(Installation,farm.pk,data.pop('installation'),status='ACTIVE')
        if not AquaticSpeciesProfile.objects.filter(exploitation=farm,species=species,active=True).exists():raise ValidationError('AQUATIC_SPECIES_REQUIRED')
        cohort_id=data.pop('cohort',None);cohort=get(Cohort,farm.pk,cohort_id) if cohort_id else None
        snapshot=brood_reference(farm.pk,species,site,cohort,data['occurred_at'],data['males'],data['females'])
        row=BroodGroup(exploitation=farm,species=species,installation=site,cohort=cohort,actor=user,source_snapshot=snapshot,**data)
        row.full_clean();row.save();return group_public(row)
    if kind in ('brood_group_patch','reproduction_patch'):
        if active_membership(user).role!='OWNER':raise PermissionDenied('OWNER_REQUIRED')
        row=get(BroodGroup if kind=='brood_group_patch' else Reproduction,farm.pk,data.pop('target'))
        if row.version!=data.pop('expected_version'):fail('STALE_RESOURCE')
        data.pop('reason')
        if kind=='brood_group_patch' and data.get('installation'):data['installation']=get(Installation,farm.pk,data['installation'],status='ACTIVE')
        for key,value in data.items():setattr(row,key,value)
        if kind=='brood_group_patch' and any(key in data for key in ('installation','males','females')):row.source_snapshot=brood_reference(farm.pk,row.species,row.installation,row.cohort,timezone.now(),row.males,row.females)
        row.version+=1;row.full_clean();row.save();return group_public(row) if kind=='brood_group_patch' else reproduce_public(row)
    if kind=='reproduction':
        group=get(BroodGroup,farm.pk,data.pop('group'),status='ACTIVE');site=get(Installation,farm.pk,data.pop('installation'),status='ACTIVE')
        if data['occurred_at']<group.occurred_at:raise ValidationError('REPRODUCTION_BEFORE_GROUP')
        brood_reference(farm.pk,group.species,site,group.cohort,data['occurred_at'],group.males,group.females)
        row=Reproduction(exploitation=farm,group=group,species=group.species,installation=site,actor=user,group_snapshot=group_public(group),**data)
        row.full_clean();row.save();return reproduce_public(row)
    if kind=='breeding_step':
        rep=get(Reproduction,farm.pk,data.pop('reproduction'),status='OPEN');site=get(Installation,farm.pk,data.pop('installation'),status='ACTIVE')
        fact_kind=data.pop('kind');pid=data.pop('parent',None);parent=get(BreedingFact,farm.pk,pid) if pid else None
        if parent and (parent.reproduction_id!=rep.pk or parent.kind not in (['SPAWN'] if fact_kind=='INCUBATION' else ['SPAWN','INCUBATION']) or fact_kind=='SPAWN'):
            raise ValidationError('INVALID_BREEDING_PARENT')
        row=create_fact(user,farm,identifier,rep,fact_kind,site,parent=parent,at=data.pop('occurred_at'),**data)
        state=timeline(rep);return fact_public(row,state)
    if kind in ('hatch_count','fry_placement'):
        hatch,rep=hatch_for(farm.pk,data);site=get(Installation,farm.pk,data.pop('installation'),status='ACTIVE');at=data.pop('occurred_at')
        data.pop('hatch');data.pop('actually_counted')
        if at<hatch.occurred_at:raise ValidationError('STOCKING_OR_COUNT_BEFORE_HATCH')
        if kind=='hatch_count':
            row=create_fact(user,farm,identifier,rep,'COUNT',site,at,parent=hatch,quality='COUNTED',**data)
            state=timeline(rep);return fact_public(row,state)
        if 'confirm_total' in data:
            total=data.pop('confirm_total');why=data.pop('confirmation_reason')
            create_fact(user,farm,uuid.uuid5(identifier,'count'),rep,'COUNT',site,at,parent=hatch,quantity=total,quality='COUNTED',reason=why,
                time_precision=data['time_precision'],business_date=data.get('business_date'))
        before=timeline(rep,at)['hatches'].get(str(hatch.pk))
        if before is None or before['voided']:fail('HATCH_NOT_ACTIVE_AT_DATE')
        if before['confirmed_total'] is None:fail('HATCH_COUNT_CONFIRMATION_REQUIRED')
        if data['quantity']>before['available_confirmed']:fail('HATCH_CONFIRMED_QUANTITY_EXCEEDED')
        from .services import execute
        from .models import Command
        from .serializers import CohortInput
        child=uuid.uuid5(identifier,'fry-cohort')
        if Cohort.objects.filter(pk=data['id']).exists() or Command.objects.filter(exploitation=farm,operation_uuid=child).exists():fail('FRY_COHORT_ID_ALREADY_USED')
        body={'operation_uuid':child,'schema_version':1,'id':data['id'],'code':data['code'],'name':data['name'],'species':rep.species_id,
            'origin':{'type':'HATCH','reference':'BREEDING:'+str(hatch.pk)},'started_at':at.isoformat(),
            'initial_count':data['quantity'],'allocations':[{'installation':str(site.pk),'count':data['quantity']}],'reason':data['reason']}
        if data.get('stage'):body['stage']=data['stage']
        checked=CohortInput(data=body);checked.is_valid(raise_exception=True)
        result,_,_=execute(user,'cohort',checked.validated_data,device=device)
        farm.refresh_from_db()
        cohort=get(Cohort,farm.pk,result['id']);ev=Event.objects.get(uuid=result['event_id'],exploitation=farm)
        row=create_fact(user,farm,identifier,rep,'PLACEMENT',site,at,parent=hatch,quantity=data['quantity'],quality='COUNTED',cohort=cohort,stock_event=ev,
            reason=data['reason'],note=data['note'],time_precision=data['time_precision'],business_date=data.get('business_date'),
            payload={'hatch':str(hatch.pk),'initial_event':str(ev.uuid),'cohort_command_uuid':str(child),'source_species':rep.species_id})
        state=timeline(rep);sync(farm.pk)
        return {**fact_public(row,state),'cohort_data':result,'hatch_availability':state['hatches'][str(hatch.pk)]}
    if kind=='breeding_void':
        if active_membership(user).role!='OWNER':raise PermissionDenied('OWNER_REQUIRED')
        target=get(BreedingFact,farm.pk,data['target']);rep=target.reproduction;at=data['occurred_at']
        state=timeline(rep)
        if target.kind=='VOID' or str(target.pk) in state['voided']:fail('BREEDING_FACT_ALREADY_VOIDED')
        if at<target.occurred_at:raise ValidationError('VOID_BEFORE_ORIGINAL')
        ev=None
        if target.kind=='PLACEMENT':
            cohort=target.cohort
            if placement_dependencies(cohort,target):fail('FRY_COHORT_HAS_DEPENDENTS')
            positions=list(cohort.positions.all())
            if len(positions)!=1 or positions[0].gross_count!=target.quantity or positions[0].reserved_count:fail('FRY_COHORT_HAS_DEPENDENTS')
            ev=event(user,identifier,cohort,'FRY_REVERSAL',at,data['reason'][:500],[(positions[0],-target.quantity)])
            sync(farm.pk);cohort.refresh_from_db();cohort.status='CLOSED';cohort.version+=1;cohort.save()
        row=create_fact(user,farm,identifier,rep,'VOID',target.installation,at,parent=target,reason=data['reason'],stock_event=ev,
            payload={'voids':str(target.pk),'cohort':str(target.cohort_id) if target.cohort_id else None})
        state=timeline(rep);return fact_public(row,state)
    raise ValidationError('UNKNOWN_BREEDING_COMMAND')
