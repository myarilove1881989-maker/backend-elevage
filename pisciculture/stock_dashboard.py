from django.utils import timezone
from rest_framework.response import Response
from .models import Installation,Cohort,StockFact
from .stock_ledger import replay,counts
from .stock_views import revision

def dashboard(request,ps,at):
    farm=request.user.exploitation_id;state=replay(farm,at)
    positions=list(ps.select_related('cohort','installation'))
    keys={(str(p.cohort_id),str(p.installation_id)) for p in positions}
    sites={p.installation_id:p.installation for p in positions}
    cohort_filter=request.query_params.get('cohort') or request.query_params.get('species')
    pending=[p for p in state['pending'].values() if p['remaining'] and
        any((c,p['installation']) in keys for c in p['eligible'])]
    gross=sum(state['gross'].get(key,0) for key in keys)
    unresolved=sum(p['remaining'] for p in pending)
    selected_sites=Installation.objects.filter(exploitation_id=farm)
    if request.query_params.get('installation'):selected_sites=selected_sites.filter(pk=request.query_params['installation'])
    if cohort_filter:selected_sites=selected_sites.filter(pk__in=list(sites))
    details=[{'installation_id':str(s.pk),'installation__name':s.name,
        'count':counts(state,str(s.pk))['live_count'],**counts(state,str(s.pk))} for s in selected_sites]
    if cohort_filter:
        details=[]
        for site in selected_sites:
            site_id=str(site.pk)
            site_gross=sum(n for (c,i),n in state['gross'].items() if i==site_id and (c,i) in keys)
            loss=sum(x['remaining'] for x in pending if x['installation']==site_id)
            details.append({'installation_id':site_id,'installation__name':site.name,'count':site_gross,
                'gross_count':site_gross,'pending_count':loss,'unassigned_credit':0,
                'quality':'UNRECONCILED' if loss else 'EXACT','lower':max(0,site_gross-loss),'upper':site_gross})
    live=gross if cohort_filter else sum(x['live_count'] for x in details)
    quality='UNRECONCILED' if cohort_filter and unresolved else 'EXACT'
    by_species={}
    for p in positions:
        row=by_species.setdefault(p.cohort.species_id,{'cohort__species_id':p.cohort.species_id,
            'cohort__species__nom':p.cohort.species.nom,'count':0,'quality':'EXACT'})
        row['count']+=state['gross'].get((str(p.cohort_id),str(p.installation_id)),0)
        if any(str(p.cohort_id) in x['eligible'] for x in pending):row['quality']='UNRECONCILED'
    mortality=sum(f.quantity for identifier,f in state['facts'].items() if f.kind=='MORTALITY' and identifier not in state['canceled'] and str(f.installation_id) in {x['installation_id'] for x in details})
    active_cohorts=Cohort.objects.filter(exploitation_id=farm,status='OPEN',pk__in=[p.cohort_id for p in positions]).distinct().count()
    metrics={'live_count':{'value':live,'unit':'fish','quality':quality,'gross_count':gross,
        'lower':max(0,gross-unresolved) if cohort_filter else live,'upper':gross if cohort_filter else live},
        'active_installations':{'value':sum(s.status=='ACTIVE' for s in selected_sites),'unit':'installation','quality':'EXACT'},
        'active_cohorts':{'value':active_cohorts,'unit':'cohort','quality':'EXACT'},
        'mortality':{'value':mortality if not cohort_filter else None,'unit':'fish','quality':'EXACT' if not cohort_filter else 'NOT_AGGREGATED'},
        'unassigned_credits':{'value':sum(x['unassigned_credit'] for x in details),'unit':'fish','quality':'UNASSIGNED'}}
    for name,unit in [('estimated_biomass','kg'),('feeding','kg'),('water',None),('harvest','kg'),('finance',None)]:
        metrics[name]={'value':None,'quality':'NOT_IMPLEMENTED','unit':unit}
    return Response({'contract_version':1,'business_revision':revision(farm),'as_of':at,
        'scope':{'activity':'AQUATIC',**{k:v for k,v in request.query_params.items() if k in {'species','cohort','installation'}}},
        'metrics':metrics,'by_species':list(by_species.values()),'by_installation':details,
        'unassigned_losses':unresolved if cohort_filter else sum(x['pending_count'] for x in details),
        'unassigned_credits':sum(x['unassigned_credit'] for x in details),
        'capabilities':{'weighings':True,'water_readings':True,'losses':True,'inventory':True,'offline_write':False}})
