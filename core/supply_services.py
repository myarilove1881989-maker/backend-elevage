"""Owner-only internal foundation services; no HTTP/offline dispatch in phase 1.

Future transports must call execute(), retaining the same UUID and payload.
All stock writes serialize under the existing farm lock. No financial write.
"""
import hashlib
import json
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from uuid import UUID
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied
from .audit import ActorContext, audit_scope, current_actor_context
from .models import Exploitation, Espece, Lot, Depense
from .permissions import require_owner
from .lot_lifecycle import ensure_open
from .supply_models import (SupplyProduct, SupplyOperation, SupplyReceipt,
                            SupplyVial, SupplyVialOpening, SupplyMovement)


def fail(code):
    raise ValidationError(code)


def quantity(value, *, signed=False, integer=False):
    if isinstance(value, (bool, float)):
        fail('SUPPLY_EXACT_NUMBER_REQUIRED')
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        fail('SUPPLY_QUANTITY_INVALID')
    if (not result.is_finite() or abs(result) >= Decimal('100000000000') or
            result == 0 or (not signed and result < 0) or
            result.as_tuple().exponent < -3 or (integer and result % 1)):
        fail('SUPPLY_QUANTITY_INVALID')
    return result


def bags_to_kg(bags, kg_per_bag):
    """Explicit conversion; no implicit/default sack capacity."""
    return quantity(quantity(bags, integer=True) * quantity(kg_per_bag))


def _owner(actor, farm_id):
    member = require_owner(actor)
    if member.exploitation_id != farm_id:
        raise PermissionDenied('SUPPLY_OUTSIDE_FARM')
    context = current_actor_context()
    if context and (context.author_id != actor.pk or context.exploitation_id != farm_id):
        raise PermissionDenied('SUPPLY_CONTEXT_MISMATCH')
    return member


def _object(model, pk, **scope):
    if isinstance(pk, bool):
        fail('SUPPLY_REFERENCE_INVALID')
    try:
        return model.objects.get(pk=pk, **scope)
    except (model.DoesNotExist, ValueError, ValidationError, TypeError):
        fail('SUPPLY_REFERENCE_OUTSIDE_FARM')


@transaction.atomic
def create_product(actor, farm_id, *, kind, name, alert_threshold='0'):
    _owner(actor, farm_id)
    farm = Exploitation.objects.select_for_update().get(pk=farm_id)
    _owner(actor, farm_id)
    with audit_scope(ActorContext(actor.pk, farm_id)):
        product = SupplyProduct.objects.create(exploitation_id=farm_id, kind=kind,
            name=name.strip(), alert_threshold=Decimal(alert_threshold))
        farm.business_revision += 1
        farm.save(update_fields=['business_revision'])
        return product


@transaction.atomic
def configure_product(actor, farm_id, product_id, *, name, alert_threshold, archived=False):
    _owner(actor, farm_id)
    farm = Exploitation.objects.select_for_update().get(pk=farm_id)
    _owner(actor, farm_id)
    product = _object(SupplyProduct, product_id, exploitation_id=farm_id)
    if type(archived) is not bool:
        fail('SUPPLY_ARCHIVE_FLAG_INVALID')
    with audit_scope(ActorContext(actor.pk, farm_id), reason_code='SUPPLY_PRODUCT_CONFIGURATION'):
        product.name = name.strip()
        product.alert_threshold = Decimal(str(alert_threshold))
        product.archived = archived
        product.save()
        farm.business_revision += 1
        farm.save(update_fields=['business_revision'])
    return product


def _balance(query):
    return query.aggregate(total=Sum('quantity'))['total'] or Decimal('0')


def _position(product, species, lot):
    return SupplyMovement.objects.filter(product=product, species=species, lot=lot)


def _vial_balance(vial):
    return _balance(SupplyMovement.objects.filter(vial=vial))


def _usable(vial, at):
    if timezone.localtime(at).date() > vial.receipt.expires_on:
        return False
    opening = SupplyVialOpening.objects.filter(vial=vial).first()
    if opening and (at < opening.opened_at or
                    (opening.usable_seconds is not None and at >= opening.opened_at + timedelta(seconds=opening.usable_seconds))):
        return False
    return True


@transaction.atomic
def stock(actor, farm_id, product_id, *, species_id=None, lot_id=None, at=None):
    _owner(actor, farm_id)
    farm = Exploitation.objects.select_for_update().get(pk=farm_id)
    product = _object(SupplyProduct, product_id, exploitation_id=farm_id)
    at = at or timezone.now()
    if timezone.is_naive(at):
        fail('SUPPLY_AWARE_DATETIME_REQUIRED')
    rows = SupplyMovement.objects.filter(product=product)
    if species_id is not None:
        _object(Espece, species_id, exploitation_id=farm_id)
        rows = rows.filter(species_id=species_id)
    if lot_id is not None:
        lot = _object(Lot, lot_id, exploitation_id=farm_id)
        if species_id is not None and lot.espece_id != species_id:
            fail('SUPPLY_LOT_SPECIES_MISMATCH')
        rows = rows.filter(lot_id=lot_id)
    physical = _balance(rows)
    if product.kind == 'FEED':
        return {'physical': physical, 'usable': physical, 'unit': 'kg',
                'below_threshold': physical <= product.alert_threshold,
                'confirmed_business_revision': farm.business_revision}
    # Vaccine stock is vial-based, not a sum of lot administration filters.
    if lot_id is not None:
        fail('SUPPLY_VACCINE_STOCK_IS_VIAL_BASED')
    vials = SupplyVial.objects.filter(receipt__product=product)
    if species_id is not None:
        vials = vials.filter(receipt__operation__movements__species_id=species_id).distinct()
    balances = [(v, _vial_balance(v)) for v in vials.select_related('receipt')]
    physical = sum((q for _, q in balances), Decimal('0'))
    usable = sum((q for v, q in balances if _usable(v, at)), Decimal('0'))
    return {'physical': physical, 'usable': usable, 'unit': 'dose',
            'below_threshold': usable <= product.alert_threshold,
            'confirmed_business_revision': farm.business_revision}


SCHEMAS = {
    'FEED_RECEIPT': ({'product','species'}, {'lot','expense','kg','bags','kg_per_bag'}),
    'VACCINE_RECEIPT': ({'product','species','manufacturer_batch','expires_on','vial_count','doses_per_vial'}, {'expense'}),
    'DISTRIBUTE': ({'product','species','lot','quantity'}, {'source_lot'}),
    'TRANSFER': ({'product','species','quantity'}, {'source_lot','target_lot'}),
    'LOSS': ({'product','species','quantity'}, {'lot','vial'}),
    'CORRECT': ({'product','species','quantity'}, {'lot','vial'}),
    'ADMINISTER': ({'product','species','lot','vial','quantity'}, set()),
    'OPEN': ({'vial'}, {'opened_at','usable_seconds'}),
    'REVERSE': ({'original'}, set()),
}


@transaction.atomic
def execute(actor, farm_id, operation_uuid, kind, payload, *, reason='', occurred_at=None):
    """One validated command = one durable effect set; conflicting retry fails.

    Owner-only for phase 1. This does not grant new operator/offline permissions.
    occurred_at records occurrence but balances always reflect validated facts.
    """
    _owner(actor, farm_id)
    farm = Exploitation.objects.select_for_update().get(pk=farm_id)
    _owner(actor, farm_id)
    if kind not in SCHEMAS or not isinstance(payload, dict):
        fail('SUPPLY_COMMAND_INVALID')
    if not isinstance(reason, str) or len(reason) > 2000:
        fail('SUPPLY_REASON_INVALID')
    required, optional = SCHEMAS[kind]
    if not required <= payload.keys() or payload.keys() - required - optional:
        fail('SUPPLY_FIELDS_INVALID')
    try:
        operation_uuid = UUID(str(operation_uuid))
        canonical = json.dumps({'kind':kind,'payload':payload,'reason':reason,
            'author':actor.pk,'occurred_at':occurred_at.isoformat() if occurred_at else None},
            sort_keys=True, separators=(',', ':'), allow_nan=False)
    except (ValueError, TypeError, AttributeError):
        fail('SUPPLY_COMMAND_INVALID')
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    prior = SupplyOperation.objects.filter(exploitation=farm, operation_uuid=operation_uuid).first()
    if prior:
        if prior.fingerprint != digest:
            fail('SUPPLY_UUID_PAYLOAD_CONFLICT')
        return prior
    now = timezone.now()
    at = occurred_at or now
    if timezone.is_naive(at) or at > now:
        fail('SUPPLY_OCCURRENCE_INVALID')
    if kind in {'LOSS','CORRECT','REVERSE'} and not reason.strip():
        fail('SUPPLY_REASON_REQUIRED')
    with audit_scope(ActorContext(actor.pk, farm_id, business_occurred_at=at),
                     reason_code='SUPPLY_' + kind, reason_text=reason):
        original = None
        if kind == 'REVERSE':
            original = _object(SupplyOperation, payload['original'], exploitation=farm)
            if original.kind in {'OPEN','REVERSE'} or SupplyOperation.objects.filter(reverses=original).exists():
                fail('SUPPLY_REVERSAL_NOT_ALLOWED')
        operation = SupplyOperation.objects.create(exploitation=farm, operation_uuid=operation_uuid,
            kind=kind, author=actor, payload=payload, fingerprint=digest, reason=reason,
            occurred_at=at, reverses=original)
        _apply(operation, payload, now)
        farm.business_revision += 1
        farm.save(update_fields=['business_revision'])
        return operation


def _apply(op, p, now):
    farm_id = op.exploitation_id
    if op.kind == 'REVERSE':
        for old in op.reverses.movements.order_by('line'):
            _move(op, old.product, old.species, old.lot, -old.quantity, 'REVERSAL', old.vial,
                  allow_closed=True, destination_lot=old.destination_lot)
        return
    if op.kind == 'OPEN':
        vial = _object(SupplyVial, p['vial'], receipt__operation__exploitation_id=farm_id)
        if _vial_balance(vial) <= 0 or not _usable(vial, now):
            fail('SUPPLY_VIAL_UNUSABLE')
        try:
            opened = datetime.fromisoformat(p['opened_at']) if p.get('opened_at') else op.occurred_at
        except (TypeError, ValueError):
            fail('SUPPLY_OPEN_DATE_INVALID')
        if timezone.is_naive(opened) or opened > now or opened < vial.receipt.operation.occurred_at:
            fail('SUPPLY_OPEN_DATE_INVALID')
        seconds = p.get('usable_seconds')
        if seconds is not None and (type(seconds) is not int or not 0 < seconds <= 31536000):
            fail('SUPPLY_OPEN_DURATION_INVALID')
        SupplyVialOpening.objects.create(vial=vial, operation=op, opened_at=opened,
            usable_seconds=seconds)
        return
    product = _object(SupplyProduct, p['product'], exploitation_id=farm_id)
    if product.archived:
        fail('SUPPLY_PRODUCT_ARCHIVED')
    species = _object(Espece, p['species'], exploitation_id=farm_id)
    lot = _object(Lot, p['lot'], exploitation_id=farm_id) if p.get('lot') else None
    if lot:
        if lot.espece_id != species.pk:
            fail('SUPPLY_LOT_SPECIES_MISMATCH')
        ensure_open(lot)
    if op.kind in {'FEED_RECEIPT','VACCINE_RECEIPT'}:
        expected = 'FEED' if op.kind == 'FEED_RECEIPT' else 'VACCINE'
        if product.kind != expected:
            fail('SUPPLY_PRODUCT_KIND_MISMATCH')
        expense = _object(Depense, p['expense'], lot__exploitation_id=farm_id) if p.get('expense') else None
        if expense:
            ensure_open(expense.lot)
        if expected == 'FEED':
            if ('kg' in p) == ('bags' in p or 'kg_per_bag' in p):
                fail('SUPPLY_EXPLICIT_UNIT_REQUIRED')
            amount = quantity(p['kg']) if 'kg' in p else bags_to_kg(p.get('bags'), p.get('kg_per_bag'))
            SupplyReceipt.objects.create(operation=op, product=product, expense=expense, quantity=amount)
            _move(op, product, species, lot, amount, 'ENTRY')
        else:
            count = quantity(p['vial_count'], integer=True)
            doses = quantity(p['doses_per_vial'], integer=True)
            if count > 1000:
                fail('SUPPLY_RECEIPT_TOO_LARGE')
            try:
                expires = date.fromisoformat(p['expires_on'])
            except (ValueError, TypeError):
                fail('SUPPLY_EXPIRY_INVALID')
            receipt = SupplyReceipt.objects.create(operation=op, product=product, expense=expense,
                manufacturer_batch=p['manufacturer_batch'], expires_on=expires,
                vial_count=int(count), doses_per_vial=int(doses), quantity=quantity(count*doses))
            for i in range(1, int(count)+1):
                vial = SupplyVial.objects.create(receipt=receipt, ordinal=i)
                _move(op, product, species, None, doses, 'ENTRY', vial)
        return
    if op.kind in {'DISTRIBUTE','TRANSFER'}:
        if product.kind != 'FEED':
            fail('SUPPLY_FEED_REQUIRED')
        source = _object(Lot, p['source_lot'], exploitation_id=farm_id) if p.get('source_lot') else None
        if source:
            ensure_open(source)
        amount = quantity(p['quantity'])
        if op.kind == 'TRANSFER':
            target = _object(Lot, p['target_lot'], exploitation_id=farm_id) if p.get('target_lot') else None
            if source == target:
                fail('SUPPLY_TRANSFER_SAME_POSITION')
            if target:
                ensure_open(target)
            _move(op, product, species, source, -amount, 'TRANSFER')
            _move(op, product, species, target, amount, 'TRANSFER')
        else:
            if not lot or source not in (None, lot):
                fail('SUPPLY_DISTRIBUTION_SCOPE_INVALID')
            # Record consumption's destination in the command, debit source position.
            _move(op, product, species, source, -amount, 'DISTRIBUTION', destination_lot=lot)
        return
    vial = _object(SupplyVial, p['vial'], receipt__product=product) if p.get('vial') else None
    amount = quantity(p['quantity'], signed=op.kind == 'CORRECT', integer=product.kind == 'VACCINE')
    if op.kind != 'CORRECT':
        amount = -amount
    if op.kind == 'ADMINISTER':
        if product.kind != 'VACCINE' or not lot or not vial or not _usable(vial, now):
            fail('SUPPLY_VIAL_UNUSABLE')
        if not SupplyVialOpening.objects.filter(vial=vial).exists():
            fail('SUPPLY_VIAL_OPENING_REQUIRED')
        if not vial.receipt.operation.movements.filter(species=species).exists():
            fail('SUPPLY_VIAL_SPECIES_MISMATCH')
    _move(op, product, species, lot, amount,
          {'CORRECT':'CORRECTION','LOSS':'LOSS','ADMINISTER':'ADMINISTRATION'}[op.kind], vial)


def _move(op, product, species, lot, amount, kind, vial=None, allow_closed=False, destination_lot=None):
    if lot and not allow_closed:
        ensure_open(lot)
    if destination_lot and not allow_closed:
        ensure_open(destination_lot)
    if product.kind == 'VACCINE':
        if vial is None:
            fail('SUPPLY_VIAL_REQUIRED')
        if kind != 'ENTRY' and not vial.receipt.operation.movements.filter(species=species).exists():
            fail('SUPPLY_VIAL_SPECIES_MISMATCH')
        balance = _vial_balance(vial)
        new_balance = balance + amount
        if new_balance < 0 or new_balance > vial.receipt.doses_per_vial:
            fail('SUPPLY_STOCK_INSUFFICIENT_OR_VIAL_CAPACITY')
    else:
        new_balance = _balance(_position(product, species, lot)) + amount
        if new_balance < 0:
            fail('SUPPLY_STOCK_INSUFFICIENT')
    return SupplyMovement.objects.create(operation=op, line=op.movements.count()+1,
        product=product, species=species, lot=lot, destination_lot=destination_lot,
        vial=vial, kind=kind, quantity=amount)
