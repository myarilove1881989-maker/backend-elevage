"""Physical vaccine workflows on the existing immutable supply ledger."""
from datetime import datetime, time, timedelta
from uuid import UUID
from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import ValidationError, PermissionDenied
from .models import Exploitation, Lot, Espece, CategorieDepense, Depense, TerrainSubmission
from .supply_models import SupplyProduct, SupplyOperation, SupplyReceipt, SupplyVial, SupplyVialOpening, SupplyMovement, SupplyCategoryBinding
from .vaccination_models import Vaccination
from .supply_services import _move, _apply, _vial_balance, _usable
from .feed_purchases import checked, command, result
from .audit import current_actor_context
from .access_policy import authorize, authorize_business_service, effective_permissions
from .lot_lifecycle import ensure_open


def actor_guard(actor, action):
    ctx=current_actor_context()
    if not ctx or ctx.author_id!=actor.pk or ctx.exploitation_id!=actor.exploitation_id:
        raise PermissionDenied('VACCINE_CONTEXT_REQUIRED')
    if ctx.source=='ONLINE':
        authorize(actor,actor.exploitation_id,action);return
    if ctx.source!='OFFLINE':raise PermissionDenied('VACCINE_REAPPROVAL_NOT_ENABLED')
    row=TerrainSubmission.objects.select_related('offline_authorization__membership__user','offline_authorization__membership__exploitation').filter(
        exploitation_id=actor.exploitation_id,client_operation_id=ctx.operation_id,author_user_id=actor.pk).first()
    if row is None:raise PermissionDenied('VACCINE_ORIGINAL_REQUIRED')
    grant=row.offline_authorization
    from .offline_permissions import fine_contract
    codes=fine_contract(grant)
    if (grant.capabilities.get('vaccine_stock_contract')!=1 or codes is None or action not in codes
            or grant.revoked_at is not None or grant.membership.version!=grant.rights_version
            or action not in effective_permissions(grant.membership)):
        raise PermissionDenied('VACCINE_RIGHTS_CHANGED')
    authorize_business_service(actor,actor.exploitation_id,action)


def integer(value, minimum=0, maximum=1000000):
    if isinstance(value,(float,bool)):raise ValidationError('EXACT_INTEGER_REQUIRED')
    return serializers.IntegerField(min_value=minimum,max_value=maximum).run_validation(value)


class VaccineInput(serializers.Serializer):
    product=serializers.UUIDField(required=False)
    new_product=serializers.DictField(required=False)
    species=serializers.IntegerField(min_value=1)
    lot=serializers.IntegerField(min_value=1,required=False,allow_null=True)
    vial_count=serializers.IntegerField(min_value=1,max_value=1000)
    doses_per_vial=serializers.IntegerField(min_value=1,max_value=1000000)
    manufacturer_batch=serializers.CharField(max_length=120)
    expires_on=serializers.DateField()
    received_on=serializers.DateField()

    def to_internal_value(self,data):
        if not isinstance(data,dict) or set(data)-set(self.fields):raise ValidationError('VACCINE_FIELDS_INVALID')
        for key in ('vial_count','doses_per_vial','species','lot'):
            if isinstance(data.get(key),(bool,float)):raise ValidationError('EXACT_INTEGER_REQUIRED')
        return super().to_internal_value(data)

    def validate(self,attrs):
        if ('product' in attrs)==('new_product' in attrs):raise ValidationError('CHOOSE_ONE_VACCINE')
        if attrs['received_on']>timezone.localdate():raise ValidationError('FUTURE_RECEIPT')
        return attrs


@transaction.atomic
def purchase(actor,body):
    actor_guard(actor,'expenses.create')
    farm=Exploitation.objects.select_for_update().get(pk=actor.exploitation_id)
    actor_guard(actor,'expenses.create')
    if set(body)-{'operation_uuid','lot','categorie','montant','date','note','vaccine_supply'}:raise ValidationError('VACCINE_PURCHASE_FIELDS_INVALID')
    identifier,digest,prior=command(actor,body.get('operation_uuid'),'VAC_PURCHASE',body)
    if prior:return result(SupplyReceipt.objects.get(operation=prior).expense_id,prior,True)
    data=VaccineInput(data=body.get('vaccine_supply'));data.is_valid(raise_exception=True);p=dict(data.validated_data)
    lot=Lot.objects.filter(pk=integer(body.get('lot'),1),exploitation=farm).first()
    if lot is None:raise ValidationError('ACCOUNTING_LOT_OUTSIDE_FARM')
    ensure_open(lot)
    category=SupplyCategoryBinding.objects.filter(category_id=integer(body.get('categorie'),1),purpose='VACCINE',category__exploitation=farm).first()
    if category is None:raise ValidationError('VACCINE_CATEGORY_REQUIRED')
    if not Espece.objects.filter(pk=p['species'],exploitation=farm).exists():raise ValidationError('SPECIES_OUTSIDE_FARM')
    if p.get('lot'):
        target=Lot.objects.filter(pk=p['lot'],exploitation=farm,espece_id=p['species']).first()
        if target is None:raise ValidationError('VACCINE_DESTINATION_INVALID')
        ensure_open(target)
    if 'new_product' in p:
        new=p.pop('new_product')
        if set(new)!={'id','name'} or not isinstance(new['name'],str):raise ValidationError('NEW_VACCINE_INVALID')
        try:pid=UUID(str(new['id']))
        except (ValueError,TypeError):raise ValidationError('NEW_VACCINE_INVALID')
        if SupplyProduct.objects.filter(pk=pid).exists():raise ValidationError('PRODUCT_UUID_ALREADY_USED')
        product=checked(SupplyProduct.objects.create,id=pid,exploitation=farm,kind='VACCINE',name=new['name'].strip())
    else:
        product=SupplyProduct.objects.filter(pk=p['product'],exploitation=farm,kind='VACCINE',archived=False).first()
        if product is None:raise ValidationError('VACCINE_OUTSIDE_FARM')
    from .serializers import DepenseSerializer
    expense=DepenseSerializer(data={key:body[key] for key in ('categorie','montant','date','note') if key in body})
    expense.is_valid(raise_exception=True)
    expense=expense.save(lot=lot,categorie=category.category)
    received=p.pop('received_on')
    p['expires_on']=p['expires_on'].isoformat();p['product']=str(product.pk);p['expense']=expense.pk
    op=checked(SupplyOperation.objects.create,exploitation=farm,operation_uuid=identifier,kind='VAC_PURCHASE',author=actor,
        payload=body,fingerprint=digest,occurred_at=timezone.make_aware(datetime.combine(received,time.min)))
    checked(_apply,op,p,timezone.now())
    return result(expense.pk,op)


def serialize(item):
    op=item.current
    return {'id':item.pk,'exploitation':item.exploitation_id,'lot':item.lot_id,'species':item.lot.espece_id,
        'product':str(item.product_id),'vaccine':item.product.name,'vaccinated_at':item.vaccinated_at.isoformat(),
        'animals':item.animals,'observations':item.observations,'operator':item.created_by_id,
        'operator_name':item.created_by.username,'version':f'vac:{op.pk}','voided':item.voided_at is not None,
        'allocations':op.payload.get('allocations',[]),'operation_uuid':str(op.operation_uuid)}


def _consume(actor,item,op,allocations,at):
    if not isinstance(allocations,list) or not 1<=len(allocations)<=100:raise ValidationError('VACCINE_ALLOCATIONS_REQUIRED')
    seen=set();total=0;losses=0
    for line in allocations:
        if not isinstance(line,dict) or set(line)-{'vial','doses','lost','opened_at','usable_seconds'}:raise ValidationError('VACCINE_ALLOCATION_FIELDS_INVALID')
        try:vid=UUID(str(line.get('vial')))
        except (ValueError,TypeError):raise ValidationError('VIAL_INVALID')
        if vid in seen:raise ValidationError('DUPLICATE_VIAL')
        seen.add(vid)
        vial=SupplyVial.objects.select_related('receipt__operation').filter(pk=vid,receipt__product=item.product).first()
        if vial is None:raise ValidationError('VIAL_OUTSIDE_FARM')
        entry=vial.receipt.operation.movements.filter(vial=vial,kind='ENTRY').first()
        if entry is None or entry.species_id!=item.lot.espece_id or entry.lot_id not in (None,item.lot_id):raise ValidationError('VIAL_ASSIGNMENT_MISMATCH')
        if at<vial.receipt.operation.occurred_at:raise ValidationError('VACCINATION_BEFORE_RECEIPT')
        used=integer(line.get('doses'),0);lost=integer(line.get('lost',0),0)
        if used+lost==0:raise ValidationError('EMPTY_VIAL_ALLOCATION')
        if used and (not _usable(vial,timezone.now()) or not _usable(vial,at)):raise ValidationError('VIAL_UNUSABLE')
        opening=SupplyVialOpening.objects.filter(vial=vial).first()
        partial_loss=used==0 and 0<lost<_vial_balance(vial)
        if partial_loss and opening is None and not line.get('opened_at'):raise ValidationError('PARTIAL_LOSS_REQUIRES_OPENING_DATE')
        if (used or partial_loss) and opening is None:
            opened=serializers.DateTimeField().run_validation(line['opened_at']) if line.get('opened_at') else at
            if opened>at or opened<vial.receipt.operation.occurred_at:raise ValidationError('VIAL_OPEN_DATE_INVALID')
            seconds=line.get('usable_seconds')
            if seconds is not None:seconds=integer(seconds,1,31536000)
            # Opening is a physical fact, kept even if the administration is annulled.
            from uuid import uuid5
            opening_op=checked(SupplyOperation.objects.create,exploitation_id=item.exploitation_id,
                operation_uuid=uuid5(op.operation_uuid,str(vid)),kind='VAC_OPEN',author=actor,payload=line,
                fingerprint=op.fingerprint,occurred_at=opened)
            checked(SupplyVialOpening.objects.create,vial=vial,operation=opening_op,opened_at=opened,usable_seconds=seconds)
            if used and (not _usable(vial,timezone.now()) or not _usable(vial,at)):raise ValidationError('VIAL_OPEN_DURATION_EXCEEDED')
        elif opening and (line.get('opened_at') or line.get('usable_seconds') is not None):
            raise ValidationError('VIAL_OPENING_ALREADY_RECORDED')
        if used:checked(_move,op,item.product,item.lot.espece,item.lot,-Decimal(used),'ADMINISTRATION',vial)
        if lost:
            actor_guard(actor,'vaccinations.loss')
            checked(_move,op,item.product,item.lot.espece,item.lot,-Decimal(lost),'LOSS',vial)
        total+=used;losses+=lost
    if (item.animals>0 and total==0) or (item.animals==0 and total>0):raise ValidationError('VACCINATION_ANIMALS_REQUIRED')
    return total,losses


@transaction.atomic
def administer(actor,body,pk=None,cancel=False):
    action='vaccinations.delete' if cancel else ('vaccinations.update' if pk else 'vaccinations.create')
    actor_guard(actor,action)
    farm=Exploitation.objects.select_for_update().get(pk=actor.exploitation_id);actor_guard(actor,action)
    required={'operation_uuid','expected_version','reason'} if pk else {'operation_uuid','lot','product','vaccinated_at','animals','allocations'}
    optional=set() if cancel else ({'vaccinated_at','animals','allocations','observations'} if pk else {'observations','species'})
    if not isinstance(body,dict) or not required<=set(body) or set(body)-required-optional:raise ValidationError('VACCINATION_FIELDS_INVALID')
    if pk and (not isinstance(body['reason'],str) or len(body['reason'].strip())<3):raise ValidationError('REASON_REQUIRED')
    payload={**body,**({'vaccination':pk} if pk else {})}
    kind='VAC_CANCEL' if cancel else ('VAC_EDIT' if pk else 'VAC_ADMIN')
    identifier,digest,prior=command(actor,body['operation_uuid'],kind,payload,body.get('reason',''))
    if prior:
        item=Vaccination.objects.get(pk=pk) if pk else Vaccination.objects.get(original=prior)
        return item
    if pk:
        item=Vaccination.objects.select_related('lot','product','current').filter(pk=pk,exploitation=farm).first()
        if item is None:raise ValidationError('VACCINATION_OUTSIDE_FARM')
        if item.voided_at or body['expected_version']!=f'vac:{item.current_id}':raise ValidationError('VACCINATION_VERSION_CONFLICT')
        ensure_open(item.lot)
    else:
        lot=Lot.objects.filter(pk=integer(body['lot'],1),exploitation=farm).first()
        if lot is None:raise ValidationError('LOT_OUTSIDE_FARM')
        ensure_open(lot)
        if body.get('species',lot.espece_id)!=lot.espece_id:raise ValidationError('VACCINE_SPECIES_MISMATCH')
        try:pid=UUID(str(body['product']))
        except (ValueError,TypeError):raise ValidationError('VACCINE_INVALID')
        product=SupplyProduct.objects.filter(pk=pid,exploitation=farm,kind='VACCINE',archived=False).first()
        if product is None:raise ValidationError('VACCINE_OUTSIDE_FARM')
    at=timezone.now() if cancel else serializers.DateTimeField().run_validation(body.get('vaccinated_at'))
    if at>timezone.now()+timedelta(minutes=5):raise ValidationError('VACCINE_FUTURE_DATE')
    op=checked(SupplyOperation.objects.create,exploitation=farm,operation_uuid=identifier,kind=kind,author=actor,
        payload=payload,fingerprint=digest,reason=body.get('reason',''),occurred_at=at)
    if pk:
        for old in item.current.movements.filter(quantity__lt=0):
            checked(_move,op,old.product,old.species,old.lot,-old.quantity,'REVERSAL',old.vial)
        # Only this version's negative lines are effective: restored lines belong
        # to the same immutable command and must not be reversed again later.
    else:
        item=Vaccination(exploitation=farm,lot=lot,product=product,original=op,current=op,created_by=actor)
    if cancel:item.voided_at=timezone.now()
    else:
        item.animals=integer(body.get('animals'),0);item.vaccinated_at=at
        item.observations=serializers.CharField(max_length=2000,allow_blank=True).run_validation(body.get('observations',''))
        _consume(actor,item,op,body.get('allocations'),at)
    item.current=op;item.save()
    return item
