"""Shared physical feed boundary for existing HTTP and signed terrain adapters."""
from decimal import Decimal
from uuid import UUID
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from rest_framework.exceptions import ValidationError, PermissionDenied
from .audit import current_actor_context
from .access_policy import authorize, authorize_business_service, effective_permissions
from .models import Exploitation, Lot, ConsommationAliment, TerrainSubmission, TerrainOutcome
from .supply_models import SupplyOperation, SupplyProduct, SupplyMovement, FeedDistributionLink
from .supply_services import _move
from .feed_purchases import checked, command
from .lot_lifecycle import ensure_open


def stock_actor(actor, action):
    context=current_actor_context()
    if context is None or context.author_id!=actor.pk or context.exploitation_id!=actor.exploitation_id:
        raise PermissionDenied('FEED_CONTEXT_REQUIRED')
    if context.source=='ONLINE':
        authorize(actor,actor.exploitation_id,action)
        return
    if context.source!='OFFLINE':raise PermissionDenied('FEED_REVIEW_REQUIRES_DEDICATED_DECISION')
    original=TerrainSubmission.objects.select_related('offline_authorization__membership__user','offline_authorization__membership__exploitation').filter(
        exploitation_id=actor.exploitation_id,client_operation_id=context.operation_id,author_user_id=actor.pk).first()
    if original is None:raise PermissionDenied('FEED_ORIGINAL_REQUIRED')
    grant=original.offline_authorization
    from .offline_permissions import fine_contract
    codes=fine_contract(grant)
    if (grant.capabilities.get('feed_stock_contract')!=1 or codes is None or grant.revoked_at is not None
        or action not in codes or action not in effective_permissions(grant.membership)
        or grant.membership.version!=grant.rights_version):
        raise PermissionDenied('FEED_RIGHTS_CHANGED')
    authorize_business_service(actor,actor.exploitation_id,action)


def version(distribution):
    link=FeedDistributionLink.objects.filter(distribution=distribution).first()
    if link is None:return None
    latest=SupplyOperation.objects.filter(exploitation_id=distribution.exploitation_id,
        kind__in=['FEED_DIST_EDIT','FEED_DIST_CANCEL'],payload__distribution=distribution.pk).order_by('-pk').first()
    return f'feed:{latest.pk if latest else link.operation_id}'


def details(distribution):
    link=FeedDistributionLink.objects.filter(distribution=distribution).first()
    return {} if link is None else {'stock_version':1,'product':str(link.product_id),'source_lot':link.source_lot_id,
        'distribution_version':version(distribution)}


@transaction.atomic
def distribute(actor,body):
    stock_actor(actor,'feed.create')
    farm=Exploitation.objects.select_for_update().get(pk=actor.exploitation_id)
    stock_actor(actor,'feed.create')
    allowed={'stock_version','operation_uuid','lot','species','product','source_lot','quantite_kg','note','distribution_at','supply_parent_uuid'}
    if set(body)-allowed or body.get('stock_version')!=1:raise ValidationError('FEED_FIELDS_INVALID')
    identifier,digest,prior=command(actor,body.get('operation_uuid'),'FEED_DISTRIBUTION',body)
    if prior:return FeedDistributionLink.objects.get(operation=prior).distribution
    from rest_framework import serializers
    lot_id=serializers.IntegerField(min_value=1).run_validation(body.get('lot'))
    lot=Lot.objects.filter(pk=lot_id,exploitation_id=farm.pk).first()
    if lot is None:raise ValidationError('FEED_LOT_OUTSIDE_FARM')
    ensure_open(lot)
    if body.get('species',lot.espece_id)!=lot.espece_id:raise ValidationError('FEED_SPECIES_MISMATCH')
    source=body.get('source_lot')
    if source is not None and (type(source) is not int or source!=lot.pk):raise ValidationError('FEED_RESERVED_FOR_ANOTHER_LOT')
    try:product_id=UUID(str(body.get('product')))
    except (ValueError,TypeError):raise ValidationError('FEED_PRODUCT_INVALID')
    product=SupplyProduct.objects.filter(pk=product_id,exploitation_id=farm.pk,kind='FEED',archived=False).first()
    if product is None:raise ValidationError('FEED_PRODUCT_OUTSIDE_FARM')
    from .supply_services import quantity
    amount=checked(quantity,body.get('quantite_kg'))
    amount=serializers.DecimalField(max_digits=10,decimal_places=3,min_value=Decimal('0.001')).run_validation(amount)
    at=serializers.DateTimeField().run_validation(body['distribution_at']) if body.get('distribution_at') else timezone.now()
    if at>timezone.now()+timezone.timedelta(minutes=5):raise ValidationError('FEED_FUTURE_DATE')
    note=serializers.CharField(max_length=255,allow_blank=True).run_validation(body.get('note',''))
    context=current_actor_context()
    if body.get('supply_parent_uuid'):
        if context.source!='OFFLINE':raise ValidationError('LOCAL_PARENT_REQUIRES_SIGNED_DECLARATION')
        original=TerrainSubmission.objects.get(exploitation_id=farm.pk,client_operation_id=context.operation_id)
        parent=str(body['supply_parent_uuid'])
        purchase=SupplyOperation.objects.filter(exploitation_id=farm.pk,operation_uuid=parent,kind='FEED_PURCHASE').first()
        if parent not in original.dependencies or purchase is None or purchase.supplyreceipt.product_id!=product.pk:
            raise ValidationError('FEED_PARENT_NOT_CONFIRMED')
    op=checked(SupplyOperation.objects.create,exploitation=farm,operation_uuid=identifier,kind='FEED_DISTRIBUTION',
        author=actor,payload=body,fingerprint=digest,occurred_at=at)
    item=ConsommationAliment.objects.create(exploitation=farm,lot=lot,aliment=product.name,
        quantite_kg=amount,note=note,date=timezone.localdate(at),distribution_at=at,created_by=actor)
    checked(_move,op,product,lot.espece,lot if source else None,-amount,'DISTRIBUTION',destination_lot=lot)
    checked(FeedDistributionLink.objects.create,distribution=item,operation=op,product=product,source_lot=lot if source else None)
    return item


@transaction.atomic
def edit_distribution(actor,pk,body,cancel=False):
    action='feed.delete' if cancel else 'feed.update'
    stock_actor(actor,action)
    farm=Exploitation.objects.select_for_update().get(pk=actor.exploitation_id)
    stock_actor(actor,action)
    required={'operation_uuid','expected_version','reason'}| (set() if cancel else {'quantite_kg'})
    if set(body)!=required:raise ValidationError('FEED_CORRECTION_FIELDS_REQUIRED')
    if not isinstance(body['reason'],str) or not body['reason'].strip():raise ValidationError('FEED_REASON_REQUIRED')
    payload={**body,'distribution':pk}
    kind='FEED_DIST_CANCEL' if cancel else 'FEED_DIST_EDIT'
    identifier,digest,prior=command(actor,body['operation_uuid'],kind,payload,body['reason'])
    item=ConsommationAliment.history.filter(pk=pk,exploitation=farm).first()
    if item is None:raise ValidationError('FEED_DISTRIBUTION_OUTSIDE_FARM')
    if prior:return item
    link=FeedDistributionLink.objects.filter(distribution=item).first()
    if link is None or item.voided_at is not None:raise ValidationError('FEED_ORIGINAL_REQUIRED')
    ensure_open(item.lot)
    if body['expected_version']!=version(item):raise ValidationError('FEED_VERSION_CONFLICT')
    from .supply_services import quantity
    desired=Decimal('0') if cancel else checked(quantity,body['quantite_kg'])
    if not cancel:
        from rest_framework import serializers
        desired=serializers.DecimalField(max_digits=10,decimal_places=3,min_value=Decimal('0.001')).run_validation(desired)
    delta=item.quantite_kg-desired
    if delta==0:raise ValidationError('FEED_QUANTITY_UNCHANGED')
    op=checked(SupplyOperation.objects.create,exploitation=farm,operation_uuid=identifier,kind=kind,
        author=actor,payload=payload,fingerprint=digest,reason=body['reason'],occurred_at=timezone.now(),
        reverses=link.operation if cancel else None)
    checked(_move,op,link.product,item.lot.espece,link.source_lot,delta,'REVERSAL' if cancel else 'CORRECTION',destination_lot=item.lot)
    if cancel:
        item.voided_at=timezone.now();item.void_decision_uuid=identifier
        item.save(update_fields=['voided_at','void_decision_uuid'])
    else:
        item.quantite_kg=desired;item.save(update_fields=['quantite_kg','updated_at'])
    return item


def snapshot(farm_id):
    """Called under the existing farm lock. Coverage prevents response-loss double folding."""
    farm=Exploitation.objects.get(pk=farm_id)
    products=[]
    for product in SupplyProduct.objects.filter(exploitation_id=farm_id,kind='FEED').order_by('id'):
        positions=list(SupplyMovement.objects.filter(product=product).values('species_id','lot_id').annotate(kg=Sum('quantity')).order_by('species_id','lot_id'))
        for position in positions:position['kg']=str(position['kg'])
        products.append({'id':str(product.pk),'name':product.name,'archived':product.archived,'positions':positions})
    coverage={}
    rows=TerrainOutcome.objects.filter(submission__exploitation_id=farm_id,business_status__in=['CONFIRMED','SUPERSEDED'],applied_at__isnull=False).order_by('submission__device_id','submission__local_sequence').values_list('submission__device_id','submission__local_sequence')
    for device,sequence in rows.iterator():
        ranges=coverage.setdefault(str(device),[])
        if ranges and ranges[-1][1]+1==sequence:ranges[-1][1]=sequence
        else:ranges.append([sequence,sequence])
    if len(products)>1000 or sum(len(ranges) for ranges in coverage.values())>10000:
        raise ValidationError('FEED_SNAPSHOT_LIMIT_REQUIRES_PAGINATION')
    return {'id':1,'exploitation':farm_id,'schema':1,'confirmed_business_revision':farm.business_revision,
        'products':products,'coverage':coverage}
