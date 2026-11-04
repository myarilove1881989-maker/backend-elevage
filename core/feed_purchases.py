"""Connected feed procurement; existing expense arithmetic and offline ledger retained."""
import hashlib
import json
from datetime import datetime, time
from decimal import Decimal
from uuid import UUID
from django.core.exceptions import ValidationError as ModelError
from django.db import transaction, IntegrityError
from django.db.models import Sum
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import ValidationError, PermissionDenied
from .models import (Exploitation, Lot, CategorieDepense, Depense, ConsommationAliment,
                     ExpenseCancellation, Espece)
from .supply_models import SupplyCategoryBinding, SupplyProduct, SupplyOperation, SupplyReceipt, SupplyMovement
from .supply_services import _apply, _move, quantity
from .serializers import DepenseSerializer
from .access_policy import authorize
from .audit import current_actor_context
from .lot_lifecycle import ensure_open


def checked(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except ModelError as exc:
        raise ValidationError(exc.messages)


def guard(actor, action):
    context = current_actor_context()
    if context and context.source == 'OFFLINE':
        from .feed_stock import stock_actor
        stock_actor(actor, action)
        return
    if not context or context.source != 'ONLINE' or context.author_id != actor.pk or context.exploitation_id != actor.exploitation_id:
        raise PermissionDenied('CONNECTED_CONTEXT_REQUIRED')
    authorize(actor, actor.exploitation_id, action)


def command(actor, identifier, kind, payload, reason=''):
    try:
        identifier=UUID(str(identifier))
        canonical=json.dumps({'kind':kind,'payload':payload,'author':actor.pk,'reason':reason},sort_keys=True,separators=(',',':'),allow_nan=False)
    except (ValueError, TypeError):
        raise ValidationError('PURCHASE_COMMAND_INVALID')
    if len(canonical)>12000:raise ValidationError('PURCHASE_COMMAND_TOO_LARGE')
    digest=hashlib.sha256(canonical.encode()).hexdigest()
    existing=SupplyOperation.objects.filter(exploitation_id=actor.exploitation_id,operation_uuid=identifier).first()
    if existing and existing.fingerprint!=digest:raise ValidationError('SUPPLY_UUID_PAYLOAD_CONFLICT')
    return identifier,digest,existing


class FeedInput(serializers.Serializer):
    product=serializers.UUIDField(required=False)
    new_product=serializers.DictField(required=False)
    species=serializers.IntegerField(min_value=1)
    lot=serializers.IntegerField(min_value=1,required=False,allow_null=True)
    kg=serializers.CharField(required=False)
    bags=serializers.CharField(required=False)
    kg_per_bag=serializers.CharField(required=False)
    received_on=serializers.DateField()

    def to_internal_value(self,data):
        if not isinstance(data,dict) or set(data)-set(self.fields):raise ValidationError('UNKNOWN_FEED_FIELDS')
        if any(isinstance(data.get(key),(bool,float)) for key in ('kg','bags','kg_per_bag')):
            raise ValidationError('EXACT_QUANTITIES_REQUIRED')
        return super().to_internal_value(data)

    def validate(self,attrs):
        if ('product' in attrs)==('new_product' in attrs):raise ValidationError('CHOOSE_ONE_FEED_PRODUCT')
        if ('kg' in attrs)==('bags' in attrs or 'kg_per_bag' in attrs):raise ValidationError('EXPLICIT_FEED_UNIT_REQUIRED')
        if 'kg' not in attrs and not {'bags','kg_per_bag'}<=attrs.keys():raise ValidationError('SACK_WEIGHT_REQUIRED')
        if attrs['received_on']>timezone.localdate():raise ValidationError('FUTURE_SUPPLY_DATE')
        return attrs


@transaction.atomic
def purchase(actor, body):
    guard(actor,'expenses.create')
    Exploitation.objects.select_for_update().get(pk=actor.exploitation_id)
    guard(actor,'expenses.create')
    if set(body)-{'operation_uuid','lot','categorie','montant','date','note','feed_supply'}:raise ValidationError('UNKNOWN_PURCHASE_FIELDS')
    identifier,digest,prior=command(actor,body.get('operation_uuid'),'FEED_PURCHASE',body)
    if prior:
        receipt=SupplyReceipt.objects.get(operation=prior)
        return result(receipt.expense_id,prior,replayed=True)
    feed=FeedInput(data=body.get('feed_supply'));feed.is_valid(raise_exception=True)
    p=feed.validated_data.copy()
    lot_id=serializers.IntegerField(min_value=1).run_validation(body.get('lot'))
    category_id=serializers.IntegerField(min_value=1).run_validation(body.get('categorie'))
    if not SupplyCategoryBinding.objects.filter(category_id=category_id,purpose='FEED',category__exploitation_id=actor.exploitation_id).exists():
        raise ValidationError('FEED_CATEGORY_REQUIRED')
    lot=Lot.objects.filter(pk=lot_id,exploitation_id=actor.exploitation_id).first()
    if lot is None:raise ValidationError('ACCOUNTING_LOT_OUTSIDE_FARM')
    ensure_open(lot)
    if not Espece.objects.filter(pk=p['species'],exploitation_id=actor.exploitation_id).exists():raise ValidationError('SPECIES_OUTSIDE_FARM')
    if p.get('lot'):
        target=Lot.objects.filter(pk=p['lot'],exploitation_id=actor.exploitation_id,espece_id=p['species']).first()
        if target is None:raise ValidationError('DESTINATION_LOT_INVALID')
        ensure_open(target)
    if 'new_product' in p:
        new=p.pop('new_product')
        if set(new)!={'id','name'} or not isinstance(new['name'],str):raise ValidationError('NEW_FEED_INVALID')
        try:product_id=UUID(str(new['id']))
        except (ValueError,TypeError):raise ValidationError('NEW_FEED_UUID_REQUIRED')
        if SupplyProduct.objects.filter(pk=product_id).exists():raise ValidationError('NEW_FEED_UUID_ALREADY_USED')
        product=checked(SupplyProduct.objects.create,id=product_id,exploitation_id=actor.exploitation_id,kind='FEED',name=new['name'].strip())
        p['product']=str(product.pk)
    else:p['product']=str(p['product'])
    serializer=DepenseSerializer(data={key:body[key] for key in ('categorie','montant','date','note') if key in body})
    serializer.is_valid(raise_exception=True)
    expense=serializer.save(lot=lot,categorie=CategorieDepense.objects.get(pk=category_id))
    received=p.pop('received_on')
    occurred=timezone.make_aware(datetime.combine(received,time.min))
    op=checked(SupplyOperation.objects.create,exploitation_id=actor.exploitation_id,operation_uuid=identifier,
        author=actor,kind='FEED_PURCHASE',payload=body,fingerprint=digest,occurred_at=occurred)
    p['expense']=expense.pk
    checked(_apply,op,p,timezone.now())
    return result(expense.pk,op)


def result(expense_id,op,replayed=False):
    expense=Depense.history.get(pk=expense_id)
    return {**DepenseSerializer(expense).data,'operation_uuid':str(op.operation_uuid),
            'stock_confirmed':expense.voided_at is None,'replayed':replayed}


def linked_receipt(actor,expense_id):
    receipt=SupplyReceipt.objects.select_related('operation','expense__lot','product').filter(
        expense_id=expense_id,operation__exploitation_id=actor.exploitation_id,operation__kind='FEED_PURCHASE').first()
    if receipt is None:raise ValidationError('CONNECTED_FEED_PURCHASE_REQUIRED')
    if receipt.expense.voided_at is not None:raise ValidationError('EXPENSE_ALREADY_CANCELLED')
    ensure_open(receipt.expense.lot)
    if ConsommationAliment.history.filter(depense_id=expense_id).exists():raise ValidationError('LINKED_CONSUMPTION_REQUIRES_REVIEW')
    entry=receipt.operation.movements.get(kind='ENTRY')
    if entry.lot_id:ensure_open(entry.lot)
    corrections=SupplyMovement.objects.filter(operation__exploitation_id=actor.exploitation_id,
        operation__kind='FEED_CORRECT',operation__payload__expense=expense_id).aggregate(n=Sum('quantity'))['n'] or Decimal('0')
    return receipt,entry,receipt.quantity+corrections


@transaction.atomic
def correct(actor,expense_id,body):
    guard(actor,'expenses.update')
    Exploitation.objects.select_for_update().get(pk=actor.exploitation_id)
    guard(actor,'expenses.update')
    if set(body)!={'operation_uuid','quantity_kg','reason'}:raise ValidationError('CORRECTION_FIELDS_REQUIRED')
    if not isinstance(body['reason'],str) or not body['reason'].strip():raise ValidationError('REASON_REQUIRED')
    payload={'expense':expense_id,**body}
    identifier,digest,prior=command(actor,body['operation_uuid'],'FEED_CORRECT',payload,body['reason'])
    if prior:return {'id':expense_id,'quantity_kg':body['quantity_kg'],'replayed':True}
    receipt,entry,current=linked_receipt(actor,expense_id)
    desired=checked(quantity,body['quantity_kg'])
    delta=desired-current
    if delta==0:raise ValidationError('QUANTITY_UNCHANGED')
    op=checked(SupplyOperation.objects.create,exploitation_id=actor.exploitation_id,operation_uuid=identifier,
        author=actor,kind='FEED_CORRECT',payload=payload,fingerprint=digest,reason=body['reason'],occurred_at=timezone.now())
    checked(_move,op,entry.product,entry.species,entry.lot,delta,'CORRECTION')
    return {'id':expense_id,'quantity_kg':str(desired),'replayed':False}


@transaction.atomic
def cancel(actor,expense,identifier,reason):
    """Called under existing cancellation transaction, permissions and farm lock."""
    guard(actor,'expenses.delete')
    Exploitation.objects.select_for_update().get(pk=actor.exploitation_id)
    guard(actor,'expenses.delete')
    payload={'expense':expense.pk}
    identifier,digest,prior=command(actor,identifier,'FEED_CANCEL',payload,reason)
    if prior:return
    receipt,entry,current=linked_receipt(actor,expense.pk)
    op=checked(SupplyOperation.objects.create,exploitation_id=actor.exploitation_id,operation_uuid=identifier,
        author=actor,kind='FEED_CANCEL',payload=payload,fingerprint=digest,reason=reason,occurred_at=timezone.now(),reverses=receipt.operation)
    checked(_move,op,entry.product,entry.species,entry.lot,-current,'REVERSAL')
    try:
        with transaction.atomic():
            ExpenseCancellation.objects.create(expense=expense,exploitation_id=actor.exploitation_id,
                operation_uuid=identifier,author=actor,reason=reason)
    except IntegrityError:
        raise ValidationError('CANCELLATION_UUID_CONFLICT')
    expense.voided_at=timezone.now();expense.void_decision_uuid=identifier
    expense.save(update_fields=['voided_at','void_decision_uuid'])
