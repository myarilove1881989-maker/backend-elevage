import hashlib,json,uuid
from collections import defaultdict
from decimal import Decimal
from fractions import Fraction
from django.core import signing
from django.db.models import Sum,Max,Q
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.exceptions import PermissionDenied,ValidationError
from core.models import Espece,Lot,CategorieDepense
from core.supply_models import SupplyProduct,SupplyMovement,SupplyCategoryBinding
from .models import FeedDistribution,FeedPurchase
from .views import endpoint,write,dated
from .services import access
from .feed_inputs import PurchaseInput,DistributionInput,ReserveInput
from .feeding import balance,purchase_public,distribution_public
from .observations import exact

def scope(request,*,financial=False):
    member=access(request.user,'aquatic.read');access(request.user,'stocks.read')
    if financial:access(request.user,'finance.read')
    allowed={'installation','cohort','product','from','to','cursor','limit'}
    if set(request.query_params)-allowed:raise ValidationError('UNKNOWN_FILTER')
    for key in ('installation','cohort','product'):
        if request.query_params.get(key):
            try:uuid.UUID(request.query_params[key])
            except (ValueError,TypeError):raise ValidationError('INVALID_FILTER')
    for key in ('from','to'):
        if request.query_params.get(key):dated(request.query_params[key])
    if request.query_params.get('from') and request.query_params.get('to') and dated(request.query_params['from'])>dated(request.query_params['to']):
        raise ValidationError('PERIOD_REVERSED')
    return member

def filtered(request,qs,*,cohort=True,date='occurred_at'):
    for key in ('installation','product'):
        if request.query_params.get(key):qs=qs.filter(**{key+'_id':request.query_params[key]})
    if cohort and request.query_params.get('cohort'):qs=qs.filter(cohort_id=request.query_params['cohort'])
    for key,lookup in [('from','gte'),('to','lte')]:
        if request.query_params.get(key):qs=qs.filter(**{date+'__'+lookup:dated(request.query_params[key])})
    return qs

def page(request,qs,encode,kind,*,date='occurred_at',recorded='recorded_at'):
    context=hashlib.sha256(json.dumps({'farm':request.user.exploitation_id,'kind':kind,
        'filters':{k:v for k,v in request.query_params.items() if k!='cursor'}},sort_keys=True).encode()).hexdigest()
    try:
        limit=int(request.query_params.get('limit',50))
        if not 1<=limit<=100:raise ValueError()
        if request.query_params.get('cursor'):
            state=signing.loads(request.query_params['cursor'],salt='aquatic-feed-pages',max_age=86400)
            if state['scope']!=context:raise ValueError()
        else:state={'scope':context,'snapshot':(qs.aggregate(n=Max(recorded))['n'] or timezone.now()).isoformat()}
    except (ValueError,signing.BadSignature):raise ValidationError('INVALID_CURSOR_OR_LIMIT')
    qs=qs.filter(**{recorded+'__lte':dated(state['snapshot'])})
    if 'last' in state:
        qs=qs.filter(Q(**{date+'__gt':dated(state['at'])})|Q(**{date:dated(state['at']),'pk__gt':state['last']}))
    rows=list(qs.order_by(date,'pk')[:limit+1]);more=len(rows)>limit;rows=rows[:limit];cursor=None
    if more:
        last=rows[-1];at=last
        for field in date.split('__'):at=getattr(at,field)
        state.update(last=str(last.pk),at=at.isoformat())
        cursor=signing.dumps(state,salt='aquatic-feed-pages',compress=True)
    return Response({'results':[encode(row) for row in rows],'next_cursor':cursor})

@endpoint(['GET'])
def catalog(request):
    member=scope(request)
    if request.query_params:raise ValidationError('CATALOG_FILTER_NOT_SUPPORTED')
    farm=request.user.exploitation_id;products=[]
    rows=SupplyMovement.objects.filter(product__exploitation_id=farm,product__kind='FEED').values('product_id','species_id','lot_id').annotate(quantity=Sum('quantity'))
    pools=defaultdict(list)
    species={x.pk:x.nom for x in Espece.objects.filter(exploitation_id=farm)}
    lots={x.pk:x.nom for x in Lot.objects.filter(exploitation_id=farm)}
    for row in rows:
        pools[row['product_id']].append({'species':row['species_id'],'lot':row['lot_id'],'quantity_kg':str(row['quantity']),
            'scope':'FARM_COMMON' if row['species_id'] is None else 'RESERVED_LOT' if row['lot_id'] else 'RESERVED_SPECIES',
            'label':'Commun à l’exploitation' if row['species_id'] is None else species.get(row['species_id'],'Espèce')+(' · '+lots.get(row['lot_id'],'Lot') if row['lot_id'] else '')})
    for item in SupplyProduct.objects.filter(exploitation_id=farm,kind='FEED').order_by('name','pk'):
        positions=pools[item.pk];total=sum((Decimal(x['quantity_kg']) for x in positions),Decimal('0'))
        products.append({'id':str(item.pk),'name':item.name,'archived':item.archived,'unit':'kg',
            'physical_kg':str(total),'common_kg':str(balance(item)),'reserves':positions,
            'aquatic_used':FeedDistribution.objects.filter(product=item).exists() or FeedPurchase.objects.filter(receipt__product=item).exists()})
    try:access(request.user,'finance.read');can_read_purchases=True
    except PermissionDenied:can_read_purchases=False
    try:
        access(request.user,'aquatic.feed.purchase');access(request.user,'expenses.create');can_purchase=True
    except PermissionDenied:can_purchase=False
    categories=CategorieDepense.objects.filter(exploitation_id=farm,supply_binding__purpose='FEED').order_by('nom')
    return Response({'products':products,'unit':'kg','can_manage_reserves':member.role=='OWNER','can_read_purchases':can_read_purchases,'can_purchase':can_purchase,
        'categories':[{'id':c.pk,'name':c.nom} for c in categories],
        'species':[{'id':k,'name':v} for k,v in species.items()],
        'lots':[{'id':x.pk,'name':x.nom,'species':x.espece_id} for x in Lot.objects.filter(exploitation_id=farm,date_fin__isnull=True).exclude(statut_production='TERMINE')]})

@endpoint(['GET','POST'])
def purchases(request):
    if request.method=='POST':return write(request,PurchaseInput,'feed_purchase')
    scope(request,financial=True)
    if request.query_params.get('installation') or request.query_params.get('cohort'):raise ValidationError('SHARED_PURCHASE_HAS_NO_POPULATION')
    qs=FeedPurchase.objects.filter(exploitation_id=request.user.exploitation_id).select_related('receipt__product','receipt__expense','receipt__operation')
    if request.query_params.get('product'):qs=qs.filter(receipt__product_id=request.query_params['product'])
    for key,lookup in [('from','gte'),('to','lte')]:
        if request.query_params.get(key):qs=qs.filter(**{'occurred_at__'+lookup:dated(request.query_params[key])})
    return page(request,qs,purchase_public,'purchases')

@endpoint(['GET','POST'])
def distributions(request):
    if request.method=='POST':return write(request,DistributionInput,'feed_distribution')
    scope(request)
    qs=filtered(request,FeedDistribution.objects.filter(exploitation_id=request.user.exploitation_id).select_related('product'))
    return page(request,qs,distribution_public,'distributions')

@endpoint(['POST'])
def reserves(request):return write(request,ReserveInput,'feed_reserve')

def summary_data(farm,*,installation=None,cohort=None,product=None,start=None,end=None):
    qs=FeedDistribution.objects.filter(exploitation_id=farm).select_related('product','supply_operation')
    for key,value in [('installation_id',installation),('product_id',product)]:
        if value:qs=qs.filter(**{key:value})
    if start:qs=qs.filter(occurred_at__gte=start)
    if end:qs=qs.filter(occurred_at__lte=end)
    unassigned=qs.filter(cohort__isnull=True).aggregate(n=Sum('quantity_kg'))['n'] or Decimal('0')
    if cohort:qs=qs.filter(cohort_id=cohort)
    total=Decimal('0');known_rest=Decimal('0');known_consumed=Decimal('0');missing=0;count=0;by_product={};by_day={};analyses=[]
    for row in qs.iterator():
        count+=1;total+=row.quantity_kg
        if row.leftovers_kg is None:missing+=1
        else:known_rest+=row.leftovers_kg;known_consumed+=row.quantity_kg-row.leftovers_kg
        item=by_product.setdefault(str(row.product_id),{'product':str(row.product_id),'name':row.product.name,'quantity':Decimal('0')})
        item['quantity']+=row.quantity_kg
        day=row.supply_operation.payload.get('business_date') or timezone.localdate(row.occurred_at).isoformat();by_day[day]=by_day.get(day,Decimal('0'))+row.quantity_kg
        analyses.append(row.analysis)
    products=sorted(by_product.values(),key=lambda x:(-x['quantity'],x['name']))
    for item in products:item['quantity_kg']=str(item.pop('quantity'))
    ratio={'status':'INSUFFICIENT_DATA','reason':'INCOMPARABLE_OR_MISSING_BIOMASS','feed_percent':None,'fcr':None}
    if analyses and all(a['status']=='INDICATIVE' for a in analyses):
        keys={(a['weight_reference'],a['biomass_kg']['numerator'],a['biomass_kg']['denominator']) for a in analyses}
        if len(keys)==1:
            weight,n,d=next(iter(keys));mass=Fraction(int(n),int(d))
            ratio={'status':'INDICATIVE','weight_reference':weight,'biomass_kg':exact(mass),
                'feed_percent':exact(Fraction(total)*100/mass),'fcr':None}
    stock=SupplyMovement.objects.filter(product__exploitation_id=farm,product__kind='FEED',species__isnull=True).aggregate(n=Sum('quantity'))['n'] or Decimal('0')
    return {'from':start.isoformat() if start else None,'to':end.isoformat() if end else None,'unit':'kg',
        'distribution_count':count,'distributed_kg':str(total),'known_leftovers_kg':str(known_rest),
        'leftovers_kg':None if missing or not count else str(known_rest),
        'estimated_consumed_kg':None if missing or not count else str(known_consumed),
        'known_consumed_kg':str(known_consumed),'unknown_consumption_count':missing,
        'consumption_status':'EMPTY' if not count else 'UNKNOWN' if missing==count else 'PARTIAL' if missing else 'ESTIMATED',
        'unassigned_distributed_kg':str(unassigned),'by_product':products,
        'by_day':[{'date':day,'quantity_kg':str(by_day[day])} for day in sorted(by_day)],
        'common_stock_kg':str(stock),'biomass_comparison':ratio}

@endpoint(['GET'])
def summary(request):
    scope(request)
    return Response(summary_data(request.user.exploitation_id,installation=request.query_params.get('installation'),
        cohort=request.query_params.get('cohort'),product=request.query_params.get('product'),
        start=dated(request.query_params['from']) if request.query_params.get('from') else None,
        end=dated(request.query_params['to']) if request.query_params.get('to') else None))

def movement_public(row):
    return {'id':row.pk,'product':str(row.product_id),'product_name':row.product.name,'quantity_kg':str(row.quantity),
        'unit':'kg','kind':row.kind,'operation_kind':row.operation.kind,'occurred_at':row.operation.occurred_at.isoformat(),
        'recorded_at':row.operation.recorded_at.isoformat(),'actor':row.operation.author_id,'operation_uuid':str(row.operation.operation_uuid),
        'species':row.species_id,'lot':row.lot_id,'scope':'FARM_COMMON' if row.species_id is None else 'RESERVED_LOT' if row.lot_id else 'RESERVED_SPECIES'}

@endpoint(['GET'])
def movements(request):
    scope(request)
    if request.query_params.get('installation') or request.query_params.get('cohort'):raise ValidationError('STOCK_MOVEMENTS_HAVE_NO_POPULATION_ALLOCATION')
    qs=SupplyMovement.objects.filter(product__exploitation_id=request.user.exploitation_id,product__kind='FEED').select_related('operation','product')
    if request.query_params.get('product'):qs=qs.filter(product_id=request.query_params['product'])
    for key,lookup in [('from','gte'),('to','lte')]:
        if request.query_params.get(key):qs=qs.filter(**{'operation__occurred_at__'+lookup:dated(request.query_params[key])})
    return page(request,qs,movement_public,'stock-movements',date='operation__occurred_at',recorded='operation__recorded_at')

def decorate_dashboard(request,response,at):
    if not FeedPurchase.objects.filter(exploitation_id=request.user.exploitation_id).exists() and not FeedDistribution.objects.filter(exploitation_id=request.user.exploitation_id).exists():return response
    try:access(request.user,'stocks.read')
    except PermissionDenied:return response
    if request.query_params.get('species'):
        response.data['feeding_unavailable_reason']='SPECIES_SCOPE_NOT_ATTRIBUTABLE'
        return response
    start=dated(request.query_params['from']) if request.query_params.get('from') else at-timezone.timedelta(days=7)
    end=dated(request.query_params['to']) if request.query_params.get('to') else at
    if start>end:raise ValidationError('PERIOD_REVERSED')
    data=summary_data(request.user.exploitation_id,installation=request.query_params.get('installation'),
        cohort=request.query_params.get('cohort'),start=start,end=end)
    response.data['feeding_overview']={key:data[key] for key in ('from','to','distributed_kg','common_stock_kg','by_product','consumption_status','estimated_consumed_kg')}
    response.data['metrics']['feeding']={'value':data['distributed_kg'],'unit':'kg','quality':'RECORDED','from':data['from'],'to':data['to']}
    return response
