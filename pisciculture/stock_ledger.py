"""Replay technical stock history. All writes remain inside the existing farm lock."""
from collections import defaultdict, deque
from django.utils import timezone
from django.db.models import Sum
from rest_framework.exceptions import ValidationError,PermissionDenied
from .models import StockFact,Position,Cohort,Installation,Event
from .services import fail,get,public

def pending_reserve(state,installation,cohort):
    return sum(p['remaining'] for p in state['pending'].values()
        if p['installation']==installation and cohort in p['eligible'])

def feasible(state):
    # Integral max flow: no pending death/correction becomes impossible to attribute.
    for installation in {p['installation'] for p in state['pending'].values() if p['remaining']}:
        demands=[p for p in state['pending'].values() if p['installation']==installation and p['remaining']]
        graph=defaultdict(dict)
        def edge(a,b,n):
            graph[a][b]=n;graph[b].setdefault(a,0)
        total=0
        for i,p in enumerate(demands):
            loss=('loss',i);edge('source',loss,p['remaining']);total+=p['remaining']
            for cohort in p['eligible']:edge(loss,('cohort',cohort),p['remaining'])
        for (cohort,site),count in state['gross'].items():
            if site==installation:edge(('cohort',cohort),'sink',count)
        flow=0
        while True:
            parent={'source':None};queue=deque(['source'])
            while queue and 'sink' not in parent:
                a=queue.popleft()
                for dest,capacity in graph[a].items():
                    if capacity>0 and dest not in parent:parent[dest]=a;queue.append(dest)
            if 'sink' not in parent:break
            amount=total-flow;node='sink'
            while parent[node] is not None:
                amount=min(amount,graph[parent[node]][node]);node=parent[node]
            node='sink'
            while parent[node] is not None:
                previous=parent[node];graph[previous][node]-=amount;graph[node][previous]+=amount;node=previous
            flow+=amount
        if flow!=total:fail('TIMELINE_CONFLICT')

def validate(state):
    if any(n<0 for n in state['gross'].values()):fail('TIMELINE_CONFLICT')
    if any(p['remaining']<0 for p in state['pending'].values()):fail('TIMELINE_CONFLICT')
    if any(p['remaining']<0 for p in state['credits'].values()):fail('TIMELINE_CONFLICT')
    feasible(state)

def counts(state,installation):
    gross=sum(n for (cohort,site),n in state['gross'].items() if site==installation)
    pending=sum(p['remaining'] for p in state['pending'].values() if p['installation']==installation)
    credit=sum(p['remaining'] for p in state['credits'].values() if p['installation']==installation)
    return {'gross_count':gross,'pending_count':pending,'unassigned_credit':credit,
        'allocated_live_count':gross-pending,'live_count':gross-pending+credit}

def eligible(state,installation):
    return sorted(c for (c,i),n in state['gross'].items() if i==installation and n>0)

def replay(farm,at=None,*,before=None):
    state={'gross':defaultdict(int),'pending':{},'credits':{},'facts':{},'canceled':set(),'source_events':[]}
    timeline=[]
    events=Event.objects.filter(exploitation_id=farm).prefetch_related('lines__position').order_by('id')
    if at:events=events.filter(occurred_at__lte=at)
    for e in events:timeline.append((e.occurred_at,e.recorded_at,0,str(e.uuid),e))
    facts=StockFact.objects.filter(exploitation_id=farm)
    if at:facts=facts.filter(occurred_at__lte=at)
    for f in facts:timeline.append((f.occurred_at,f.recorded_at,1,str(f.pk),f))
    for _,_,kind,identifier,item in sorted(timeline,key=lambda x:x[:4]):
        if before and (item.occurred_at,item.recorded_at)>=before:continue
        if kind==0:
            lines=list(item.lines.all())
            if item.type in ('TRANSFER','HARVEST'):
                for line in lines:
                    if line.delta_count<0:
                        p=line.position;key=(str(p.cohort_id),str(p.installation_id))
                        available=max(0,state['gross'][key]-pending_reserve(state,key[1],key[0]))
                        if -line.delta_count>available:fail('TIMELINE_CONFLICT')
            for line in lines:
                p=line.position;state['gross'][(str(p.cohort_id),str(p.installation_id))]+=line.delta_count
            state['source_events'].append(identifier)
        else:
            site=str(item.installation_id);cohort=str(item.cohort_id) if item.cohort_id else None
            payload=item.payload;quantity=item.quantity;parent=str(item.parent_id) if item.parent_id else None
            if item.kind in ('MORTALITY','INVENTORY'):
                if item.kind=='INVENTORY':
                    current=counts(state,site)
                    if current['live_count']!=payload['before_live_count']:fail('TIMELINE_CONFLICT')
                    delta=payload['delta']
                    if delta!=payload['observed_count']-current['live_count']:fail('TIMELINE_CONFLICT')
                    if delta and current['unassigned_credit']:fail('INVENTORY_CREDIT_PENDING')
                else:delta=-quantity
                if delta<0 and cohort:
                    state['gross'][(cohort,site)]+=delta
                elif delta<0:
                    if item.kind=='MORTALITY' and counts(state,site)['unassigned_credit']:fail('TIMELINE_CONFLICT')
                    candidates=eligible(state,site)
                    if candidates!=payload['eligible']:fail('TIMELINE_CONFLICT')
                    state['pending'][identifier]={'id':identifier,'installation':site,'eligible':candidates,
                        'quantity':quantity,'remaining':quantity,'kind':item.kind,'occurred_at':item.occurred_at.isoformat()}
                elif delta>0:
                    state['credits'][identifier]={'id':identifier,'installation':site,
                        'quantity':quantity,'remaining':quantity,'occurred_at':item.occurred_at.isoformat()}
            elif item.kind=='RECONCILE':
                source=state['pending'].get(parent)
                if not source or parent in state['canceled']:fail('TIMELINE_CONFLICT')
                for allocation in payload['allocations']:
                    c=allocation['cohort'];n=allocation['quantity']
                    if c not in source['eligible']:fail('INELIGIBLE_COHORT')
                    source['remaining']-=n;state['gross'][(c,site)]-=n
            elif item.kind=='ASSIGN_CREDIT':
                source=state['credits'].get(parent)
                if not source or parent in state['canceled']:fail('TIMELINE_CONFLICT')
                source['remaining']-=quantity;state['gross'][(cohort,site)]+=quantity
            elif item.kind=='CANCEL':
                target=str(item.reverses_id)
                original=state['facts'].get(target)
                if not original or target in state['canceled'] or original.kind=='CANCEL':fail('TIMELINE_CONFLICT')
                original_cohort=str(original.cohort_id) if original.cohort_id else None
                original_parent=str(original.parent_id) if original.parent_id else None
                if original.kind=='MORTALITY' and original_cohort:
                    state['gross'][(original_cohort,site)]+=original.quantity
                elif target in state['pending']:
                    if state['pending'][target]['remaining']!=original.quantity:fail('DEPENDENT_RECONCILIATIONS')
                    state['pending'][target]['remaining']=0
                elif target in state['credits']:
                    if state['credits'][target]['remaining']!=original.quantity:fail('DEPENDENT_CREDIT_ALLOCATIONS')
                    state['credits'][target]['remaining']=0
                elif original.kind=='RECONCILE':
                    if original_parent in state['canceled']:fail('TIMELINE_CONFLICT')
                    source=state['pending'][original_parent]
                    for allocation in original.payload['allocations']:
                        source['remaining']+=allocation['quantity']
                        state['gross'][(allocation['cohort'],site)]+=allocation['quantity']
                elif original.kind=='ASSIGN_CREDIT':
                    if original_parent in state['canceled']:fail('TIMELINE_CONFLICT')
                    state['credits'][original_parent]['remaining']+=original.quantity
                    state['gross'][(original_cohort,site)]-=original.quantity
                state['canceled'].add(target)
            state['facts'][identifier]=item
        validate(state)
    return state

def sync(farm):
    state=replay(farm);changed=set()
    for p in Position.objects.filter(exploitation_id=farm).select_related('cohort'):
        key=(str(p.cohort_id),str(p.installation_id))
        gross=state['gross'].get(key,0)
        reserved=min(gross,pending_reserve(state,key[1],key[0]))
        if (p.gross_count,p.reserved_count)!=(gross,reserved):
            p.gross_count=gross;p.reserved_count=reserved;p.version+=1;p.save();changed.add(p.cohort_id)
    for c in Cohort.objects.filter(pk__in=changed):
        c.version+=1;c.save()
    return state

def stock_public(f,state):
    result=public(f)
    result['canceled']=str(f.pk) in state['canceled']
    result['remaining']=state['pending'].get(str(f.pk),state['credits'].get(str(f.pk),{})).get('remaining')
    return result

def apply(user,kind,args,identifier,farm):
    from core.access_policy import active_membership
    if kind!='mortality' and active_membership(user).role!='OWNER':raise PermissionDenied('OWNER_REQUIRED')
    expected=args.pop('expected_revision',None)
    if expected is not None and expected!=farm.business_revision:fail('STALE_RESOURCE')
    at=args.pop('occurred_at');reason=args.pop('reason');note=args.pop('note','')
    # Recorded order makes the proposed fact last among equal effective timestamps.
    state=replay(farm.pk,at);parent=None;reverses=None;cohort=None;payload={}
    if kind=='mortality':
        site=get(Installation,farm.pk,args['installation'],status='ACTIVE')
        quantity=args['quantity'];fact_kind='SUSPICION' if args['suspected'] else 'MORTALITY'
        if args.get('cohort'):
            cohort=get(Cohort,farm.pk,args['cohort'],status='OPEN')
            if not Position.objects.filter(cohort=cohort,installation=site).exists():raise ValidationError('COHORT_NOT_IN_INSTALLATION')
        elif fact_kind=='MORTALITY':
            if counts(state,str(site.pk))['unassigned_credit']:fail('UNASSIGNED_CREDIT_REQUIRES_ASSIGNMENT')
            payload['eligible']=eligible(state,str(site.pk))
    elif kind=='inventory':
        site=get(Installation,farm.pk,args['installation'],status='ACTIVE')
        current=counts(state,str(site.pk));delta=args['observed_count']-current['live_count']
        if delta and current['unassigned_credit']:fail('INVENTORY_CREDIT_PENDING')
        quantity=abs(delta);fact_kind='INVENTORY'
        payload={'observed_count':args['observed_count'],'before_live_count':current['live_count'],'delta':delta}
        if delta<0:payload['eligible']=eligible(state,str(site.pk))
    elif kind in ('reconcile','assign_credit'):
        parent=get(StockFact,farm.pk,args['pending'] if kind=='reconcile' else args['credit'])
        if at<parent.occurred_at:fail('TIMELINE_CONFLICT')
        site=parent.installation
        if kind=='reconcile':
            if str(parent.pk) not in state['pending'] or str(parent.pk) in state['canceled']:fail('PENDING_LOSS_REQUIRED')
            allocations=[]
            for item in args['allocations']:
                c=get(Cohort,farm.pk,item['cohort'],status='OPEN')
                allocations.append({'cohort':str(c.pk),'quantity':item['quantity']})
            payload={'allocations':allocations};quantity=sum(x['quantity'] for x in allocations);fact_kind='RECONCILE'
        else:
            if str(parent.pk) not in state['credits'] or str(parent.pk) in state['canceled']:fail('UNASSIGNED_CREDIT_REQUIRED')
            cohort=get(Cohort,farm.pk,args['cohort'],status='OPEN')
            Position.objects.get_or_create(exploitation=farm,cohort=cohort,installation=site)
            quantity=args['quantity'];fact_kind='ASSIGN_CREDIT'
    else:
        reverses=get(StockFact,farm.pk,args['target']);site=reverses.installation
        if reverses.kind=='CANCEL':raise ValidationError('COMPENSATION_CANNOT_BE_CANCELED')
        if at<reverses.occurred_at:fail('TIMELINE_CONFLICT')
        if StockFact.objects.filter(reverses=reverses).exists():fail('ALREADY_CANCELED')
        if reverses.quantity==0:fail('NO_EFFECT_TO_CANCEL')
        affected=[str(reverses.cohort_id)] if reverses.cohort_id else []
        if reverses.kind=='RECONCILE':affected += [x['cohort'] for x in reverses.payload['allocations']]
        if Cohort.objects.filter(pk__in=affected,status='CLOSED').exists():fail('COHORT_CLOSED')
        quantity=reverses.quantity;fact_kind='CANCEL'
    if site.status!='ACTIVE':fail('INSTALLATION_INACTIVE')
    fact=StockFact(exploitation=farm,actor=user,operation_uuid=identifier,installation=site,
        cohort=cohort,parent=parent,reverses=reverses,kind=fact_kind,quantity=quantity,
        occurred_at=at,reason=reason,note=note,payload=payload)
    fact.full_clean();fact.save()
    state=sync(farm.pk)
    return {'fact':stock_public(fact,state),'installation_counts':counts(state,str(site.pk)),
        'pending':[p for p in state['pending'].values() if p['remaining']],
        'credits':[p for p in state['credits'].values() if p['remaining']]}

def cohort_has_pending(farm,cohort):
    return any(p['remaining'] and str(cohort) in p['eligible'] for p in replay(farm)['pending'].values())
