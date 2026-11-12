"""Online aquatic adapters over the one shared physical supply ledger."""
import hashlib,json
from decimal import Decimal
from fractions import Fraction
from django.core.serializers.json import DjangoJSONEncoder
from django.core.exceptions import ValidationError as ModelError
from django.db.models import Sum
from django.utils import timezone
from rest_framework.exceptions import ValidationError,PermissionDenied
from core.models import Espece,Lot,CategorieDepense,Depense
from core.supply_models import SupplyProduct,SupplyOperation,SupplyReceipt,SupplyMovement,SupplyCategoryBinding
from core.supply_services import _move
from core.lot_lifecycle import ensure_open
from core.serializers import DepenseSerializer
from .models import FeedPurchase,FeedDistribution,Installation,Cohort,Observation
from .services import access,get,public,fail
from .stock_ledger import replay
from .observations import count_context,exact

def balance(product,species=None,lot=None):
    return SupplyMovement.objects.filter(product=product,species=species,lot=lot).aggregate(n=Sum('quantity'))['n'] or Decimal('0')

def move(op,product,species,lot,amount,kind):
    try:return _move(op,product,species,lot,amount,kind)
    except ModelError as error:
        if 'SUPPLY_STOCK_INSUFFICIENT' in str(error):fail('INSUFFICIENT_FEED_STOCK')
        raise ValidationError('FEED_MOVEMENT_INVALID')

def validate_pool(product,species=None,lot=None):
    total=Decimal('0')
    rows=SupplyMovement.objects.filter(product=product,species=species,lot=lot).select_related('operation').order_by(
        'operation__occurred_at','operation__recorded_at','operation_id','line')
    for row in rows:
        total+=row.quantity
        if total<0:fail('FEED_TIMELINE_CONFLICT')

def operation(user,farm,identifier,kind,args,at):
    if SupplyOperation.objects.filter(exploitation=farm,operation_uuid=identifier).exists():fail('UUID_ALREADY_USED_IN_SHARED_STOCK')
    payload=json.loads(json.dumps(args,cls=DjangoJSONEncoder))
    digest=hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return SupplyOperation.objects.create(exploitation=farm,author=user,operation_uuid=identifier,
        kind=kind,payload=payload,fingerprint=digest,occurred_at=at,reason=args.get('reason',''))

def product(farm,identifier):
    return get(SupplyProduct,farm,identifier,kind='FEED',archived=False)

def purchase(user,farm,args,identifier):
    member=access(user,'expenses.create')
    if ('new_product' in args or 'new_category' in args) and member.role!='OWNER':raise PermissionDenied('OWNER_REQUIRED_FOR_CATALOG')
    at=args['occurred_at'];op=operation(user,farm,identifier,'AQUA_FEED_PURCHASE',args,at)
    if 'new_product' in args:
        new=args['new_product']
        if SupplyProduct.objects.filter(exploitation=farm,kind='FEED',name__iexact=new['name']).exists():fail('FEED_PRODUCT_ALREADY_EXISTS')
        item=SupplyProduct.objects.create(exploitation=farm,id=new['id'],kind='FEED',name=new['name'])
    else:item=product(farm.pk,args['product'])
    if 'new_category' in args:
        if CategorieDepense.objects.filter(exploitation=farm,nom__iexact=args['new_category']).exists():fail('EXPENSE_CATEGORY_ALREADY_EXISTS')
        category=CategorieDepense.objects.create(exploitation=farm,nom=args['new_category'])
        SupplyCategoryBinding.objects.create(category=category,purpose='FEED')
    else:
        category=get(CategorieDepense,farm.pk,args['category'])
        if not SupplyCategoryBinding.objects.filter(category=category,purpose='FEED').exists():raise ValidationError('FEED_CATEGORY_REQUIRED')
    serializer=DepenseSerializer(data={'categorie':category.pk,'montant':str(args['amount']),
        'date':args.get('business_date',timezone.localdate(at)).isoformat(),'note':args['note']})
    serializer.is_valid(raise_exception=True)
    expense=serializer.save(lot=None,shared_exploitation=farm,created_by=user)
    receipt=SupplyReceipt.objects.create(operation=op,product=item,expense=expense,quantity=args['quantity_kg'])
    move(op,item,None,None,args['quantity_kg'],'ENTRY');validate_pool(item)
    fact=FeedPurchase(exploitation=farm,actor=user,command_uuid=identifier,receipt=receipt,occurred_at=at)
    fact.full_clean();fact.save()
    return purchase_public(fact)

def pool(farm,payload):
    species=get(Espece,farm,payload['species']) if payload.get('species') else None
    lot=get(Lot,farm,payload['lot']) if payload.get('lot') else None
    if lot:
        ensure_open(lot)
        if lot.espece_id!=species.pk:raise ValidationError('RESERVE_SPECIES_MISMATCH')
    return species,lot

def reserve(user,farm,args,identifier):
    if access(user,'aquatic.read').role!='OWNER':raise PermissionDenied('OWNER_REQUIRED')
    item=product(farm.pk,args['product']);source=pool(farm.pk,args['source']);target=pool(farm.pk,args['target'])
    op=operation(user,farm,identifier,'AQUA_FEED_TRANSFER',args,args['occurred_at'])
    move(op,item,*source,-args['quantity_kg'],'TRANSFER')
    move(op,item,*target,args['quantity_kg'],'TRANSFER')
    validate_pool(item,*source);validate_pool(item,*target)
    return {'operation_uuid':str(identifier),'product':str(item.pk),'quantity_kg':str(args['quantity_kg']),
        'source':args['source'],'target':args['target'],'common_stock_kg':str(balance(item)),'unit':'kg'}

def signature(context):
    return sorted((row['position'],row['gross_count'],tuple(sorted(row.get('source_events',[]))),
        tuple(sorted(row.get('source_stock_facts',[])))) for row in context['positions'])

def biomass(user,site,cohort,at,amount,reference=None):
    unavailable=lambda reason:{'status':'INSUFFICIENT_DATA','reason':reason,'biomass_kg':None,'feed_percent':None,'fcr':None}
    qs=Observation.objects.filter(exploitation_id=user.exploitation_id,kind='WEIGHT',occurred_at__lte=at)
    qs=qs.filter(cohort=cohort,installation__isnull=True) if cohort else qs.filter(installation=site,cohort__isnull=True)
    if reference:
        weight=qs.filter(pk=reference).first()
        if weight is None:raise ValidationError('WEIGHT_REFERENCE_SCOPE_OR_DATE_MISMATCH')
    else:weight=qs.order_by('-occurred_at','-recorded_at','-pk').first()
    if weight is None:return unavailable('NO_WEIGHT_FOR_EXACT_SCOPE')
    current=count_context(user.exploitation_id,at,cohort=cohort,installation=None if cohort else site)
    if current['quality']!='EXACT':return unavailable(current['quality'])
    original=weight.result['count_reference']
    if original['quality']!='EXACT' or signature(current)!=signature(original):return unavailable('COUNT_CONTEXT_CHANGED')
    policy=weight.result.get('freshness_policy')
    if not policy:return unavailable('FRESHNESS_NOT_CONFIGURED')
    if Decimal(str((at-weight.occurred_at).total_seconds()))>Decimal(policy['upper'])*86400:return unavailable('WEIGHT_STALE')
    ratio=weight.result['mean_g'];mean=Fraction(int(ratio['numerator']),int(ratio['denominator']))
    mass=Fraction(current['gross_count'])*mean/1000
    if mass<=0:return unavailable('NO_RELIABLE_BIOMASS')
    return {'status':'INDICATIVE','weight_reference':str(weight.pk),'weight_at':weight.occurred_at.isoformat(),
        'count_reference':current,'biomass_kg':exact(mass),'feed_percent':exact(Fraction(amount)*100/mass),
        'label':'Aliment distribué / biomasse estimée à la date, indicatif','fcr':None}

def distribute(user,farm,args,identifier):
    site=get(Installation,farm.pk,args['installation'],status='ACTIVE')
    cohort=get(Cohort,farm.pk,args['cohort'],status='OPEN') if args.get('cohort') else None
    at=args['occurred_at'];state=replay(farm.pk,at)
    site_id=str(site.pk)
    present={c:n for (c,i),n in state['gross'].items() if i==site_id and n>0}
    if cohort and str(cohort.pk) not in present:fail('COHORT_NOT_PRESENT_AT_DISTRIBUTION_DATE')
    if not present:fail('NO_POPULATION_AT_DISTRIBUTION_DATE')
    item=product(farm.pk,args['product'])
    analysis=biomass(user,site,cohort,at,args['quantity_kg'],args.get('weight_reference'))
    op=operation(user,farm,identifier,'AQUA_FEED_DISTRIB',args,at)
    move(op,item,None,None,-args['quantity_kg'],'DISTRIBUTION');validate_pool(item)
    fact=FeedDistribution(exploitation=farm,actor=user,command_uuid=identifier,supply_operation=op,
        installation=site,cohort=cohort,product=item,quantity_kg=args['quantity_kg'],
        leftovers_kg=args['leftovers_kg'],occurred_at=at,note=args['note'],analysis=analysis)
    fact.full_clean();fact.save()
    return distribution_public(fact)

def purchase_public(fact):
    receipt=fact.receipt;expense=receipt.expense
    return {'id':str(fact.pk),'operation_uuid':str(fact.command_uuid),'occurred_at':fact.occurred_at.isoformat(),
        'recorded_at':fact.recorded_at.isoformat(),'actor':fact.actor_id,
        'time_precision':receipt.operation.payload.get('time_precision','DATETIME'),'business_date':receipt.operation.payload.get('business_date'),'product':str(receipt.product_id),
        'product_name':receipt.product.name,'quantity_kg':str(receipt.quantity),'unit':'kg','expense_id':expense.pk,
        'amount':str(expense.montant),'category':expense.categorie_id,'note':expense.note,
        'supply_receipt':receipt.pk,'supply_operation':str(receipt.operation.operation_uuid),
        'common_stock_kg':str(balance(receipt.product))}

def distribution_public(fact):
    return {**public(fact),'operation_uuid':str(fact.command_uuid),'product_name':fact.product.name,'unit':'kg',
        'estimated_consumed_kg':str(fact.quantity_kg-fact.leftovers_kg) if fact.leftovers_kg is not None else None,
        'consumption_status':'ESTIMATED' if fact.leftovers_kg is not None else 'UNKNOWN',
        'time_precision':fact.supply_operation.payload.get('time_precision','DATETIME'),'business_date':fact.supply_operation.payload.get('business_date'),
        'common_stock_kg':str(balance(fact.product))}
