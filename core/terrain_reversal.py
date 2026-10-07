"""Compensate applied effects without deleting the original facts or allocations."""
from decimal import Decimal
from django.db.models import Sum
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from .models import (Lot, Mouvement, Vente, VenteOeufs, MouvementOeufs,
    Lettrage, EncaissementTerrain, TerrainStockAdjustment, Depense, ConsommationAliment, PeseeProduction,
    CollecteOeufs, Achat, TerrainEntityMapping, AuditEvent, Client, Payment)


def void(entity, decision_uuid):
    if entity.voided_at is not None:
        raise ValidationError('BUSINESS_EFFECT_ALREADY_REVERSED')
    entity.voided_at = timezone.now()
    entity.void_decision_uuid = decision_uuid
    entity.save(update_fields=['voided_at','void_decision_uuid'])


def compensate(row, data, lot, quantity, *, kind='ANIMAL', collection=None):
    if lot.exploitation_id != row.exploitation_id:
        raise ValidationError('REVERSAL_OUTSIDE_FARM')
    return TerrainStockAdjustment.objects.create(exploitation_id=row.exploitation_id,
        submission=row, decision_uuid=data['decision_uuid'], lot=lot, kind=kind,
        signed_quantity=quantity, collection=collection)


def reverse_effect(row, outcome, data):
    if outcome.applied_at is None or outcome.business_status in ('NOT_APPLIED','SUPERSEDED'):
        raise ValidationError('APPLIED_BUSINESS_EFFECT_REQUIRED')
    try:
        pk = int(outcome.server_entity_id)
    except (TypeError, ValueError):
        raise ValidationError('APPLIED_BUSINESS_EFFECT_REQUIRED')
    effects = {}
    if row.entity_type in ('VENTE_ANIMAUX','VENTE_OEUFS'):
        if row.entity_type == 'VENTE_OEUFS':
            eggs = VenteOeufs._base_manager.select_for_update().get(pk=pk, exploitation_id=row.exploitation_id)
            sale = Vente._base_manager.select_for_update().get(pk=eggs.vente_id, lot__exploitation_id=row.exploitation_id)
        else:
            eggs = None
            sale = Vente._base_manager.select_for_update().get(pk=pk, lot__exploitation_id=row.exploitation_id)
        if sale.voided_at is not None:
            raise ValidationError('BUSINESS_EFFECT_ALREADY_REVERSED')
        if sale.lettrages.exists():
            raise ValidationError('SALE_HAS_ACTIVE_ALLOCATIONS')
        lot = Lot.objects.select_for_update().get(pk=sale.lot_id, exploitation_id=row.exploitation_id)
        if eggs:
            movement = MouvementOeufs.objects.select_for_update().get(vente_oeufs=eggs)
            allocations = list(movement.affectations.select_related('collecte').all())
            if sum(item.quantite for item in allocations) != eggs.nombre_oeufs:
                raise ValidationError('EGG_REVERSAL_ORIGINS_REQUIRED')
            for item in allocations:
                if item.collecte.lot_id != lot.pk or item.collecte.exploitation_id != row.exploitation_id:
                    raise ValidationError('REVERSAL_OUTSIDE_FARM')
                compensate(row,data,lot,item.quantite,kind='EGG',collection=item.collecte)
            void(eggs,data['decision_uuid'])
            effects['stock_restored'] = eggs.nombre_oeufs
        else:
            try:
                movement = sale.mouvement_animal
            except Mouvement.DoesNotExist:
                raise ValidationError('SALE_STOCK_LINK_REQUIRED')
            if movement.quantite_signee != -sale.quantite:
                raise ValidationError('SALE_STOCK_LINK_INCONSISTENT')
            compensate(row,data,lot,-movement.quantite_signee)
            effects['stock_restored'] = -movement.quantite_signee
        void(sale,data['decision_uuid'])
        outcome.affected_lot_ids = [lot.pk]
        outcome.business_status = 'SUPERSEDED'
        outcome.reason_code = 'BUSINESS_EFFECT_REVERSED'
        effects['recognized_sale_voided'] = sale.pk
    elif row.entity_type in ('MORTALITE','DON','VOL'):
        movement = Mouvement.objects.select_for_update().get(pk=pk, lot__exploitation_id=row.exploitation_id)
        if movement.quantite_signee >= 0:
            raise ValidationError('REMOVAL_REVERSAL_REQUIRED')
        lot = Lot.objects.select_for_update().get(pk=movement.lot_id, exploitation_id=row.exploitation_id)
        compensate(row,data,lot,-movement.quantite_signee)
        outcome.affected_lot_ids = [lot.pk]
        outcome.business_status = 'SUPERSEDED'
        outcome.reason_code = 'BUSINESS_EFFECT_REVERSED'
        effects['stock_restored'] = -movement.quantite_signee
    elif row.entity_type == 'CLIENT':
        customer = Client._base_manager.select_for_update().get(pk=pk,exploitation_id=row.exploitation_id)
        if Vente.objects.filter(client=customer).exists() or Payment.objects.filter(client=customer).exists():
            raise ValidationError('CLIENT_HAS_ACTIVE_BUSINESS_FACTS')
        void(customer,data['decision_uuid'])
        outcome.business_status='SUPERSEDED'; outcome.reason_code='BUSINESS_EFFECT_REVERSED'
        effects['client_archived']=customer.pk
    elif row.entity_type == 'COLLECTE_OEUFS':
        collection = CollecteOeufs._base_manager.select_for_update().get(pk=pk,exploitation_id=row.exploitation_id)
        lot = Lot.objects.select_for_update().get(pk=collection.lot_id,exploitation_id=row.exploitation_id)
        assigned = collection.affectations_sortie.aggregate(total=Sum('quantite'))['total'] or 0
        returned = TerrainStockAdjustment.objects.filter(collection=collection,kind='EGG',signed_quantity__gt=0
            ).aggregate(total=Sum('signed_quantity'))['total'] or 0
        from .egg_services import get_egg_stock
        quantity = collection.nombre_commercialisable
        if assigned != returned or get_egg_stock(lot.exploitation,lot) < quantity:
            raise ValidationError('COLLECTION_HAS_ACTIVE_EGG_EXITS')
        if quantity: compensate(row,data,lot,-quantity,kind='EGG',collection=collection)
        void(collection,data['decision_uuid'])
        outcome.affected_lot_ids = [lot.pk]
        outcome.business_status = 'SUPERSEDED'; outcome.reason_code = 'BUSINESS_EFFECT_REVERSED'
        effects['egg_stock_removed'] = quantity
    elif row.entity_type in ('ACHAT','NAISSANCE'):
        if row.entity_type == 'ACHAT':
            origin = Achat._base_manager.select_for_update().get(pk=pk,exploitation_id=row.exploitation_id)
            lot = Lot.objects.select_for_update().get(pk=origin.lot_id,exploitation_id=row.exploitation_id)
            entry = AuditEvent.objects.filter(operation_id=row.client_operation_id,
                entity_type='core.mouvement',action='CREATE').first()
            if not entry:
                raise ValidationError('PURCHASE_STOCK_LINK_REQUIRED')
            movement = lot.mouvements.get(pk=int(entry.entity_id),type_mouvement='ACHAT')
            if movement.quantite_signee != origin.quantite:
                raise ValidationError('PURCHASE_STOCK_LINK_INCONSISTENT')
        else:
            origin = None
            movement = Mouvement.objects.select_for_update().get(pk=pk,type_mouvement='NAISSANCE',lot__exploitation_id=row.exploitation_id)
            lot = Lot.objects.select_for_update().get(pk=movement.lot_id,exploitation_id=row.exploitation_id)
        if lot.stock != movement.quantite_signee:
            raise ValidationError('LOT_ORIGIN_HAS_ACTIVE_EFFECTS')
        for model in (Vente,Depense,ConsommationAliment,PeseeProduction,CollecteOeufs):
            if model.objects.filter(lot=lot).exists():
                raise ValidationError('LOT_ORIGIN_HAS_ACTIVE_EFFECTS')
        if Achat.objects.filter(lot=lot).exclude(pk=origin.pk if origin else None).exists():
            raise ValidationError('LOT_ORIGIN_HAS_ACTIVE_EFFECTS')
        for other in lot.mouvements.exclude(pk=movement.pk):
            if other.vente_id and Vente._base_manager.filter(pk=other.vente_id,voided_at__isnull=False).exists():
                continue
            if not TerrainEntityMapping.objects.filter(exploitation_id=row.exploitation_id,
                entity_type=other.type_mouvement,server_entity_id=other.pk,
                submission__outcome__business_status='SUPERSEDED').exists():
                raise ValidationError('LOT_ORIGIN_HAS_ACTIVE_EFFECTS')
        compensate(row,data,lot,-movement.quantite_signee)
        if origin: void(origin,data['decision_uuid'])
        lot.statut_production='TERMINE'; lot.date_fin=timezone.localdate()
        lot.save(update_fields=['statut_production','date_fin'])
        outcome.affected_lot_ids=[lot.pk]; outcome.business_status='SUPERSEDED'
        outcome.reason_code='BUSINESS_EFFECT_REVERSED'
        effects['lot_origin_reversed']=lot.pk
    elif row.entity_type in ('DEPENSE', 'ALIMENTATION', 'PESEE'):
        model = {'DEPENSE':Depense, 'ALIMENTATION':ConsommationAliment, 'PESEE':PeseeProduction}[row.entity_type]
        entity = model._base_manager.select_for_update().get(pk=pk, lot__exploitation_id=row.exploitation_id)
        Lot.objects.select_for_update().get(pk=entity.lot_id, exploitation_id=row.exploitation_id)
        if row.entity_type == 'DEPENSE' and ConsommationAliment.objects.filter(depense_id=pk).exists():
            raise ValidationError('EXPENSE_HAS_ACTIVE_FEED_DECLARATION')
        void(entity, data['decision_uuid'])
        outcome.business_status = 'SUPERSEDED'
        outcome.reason_code = 'BUSINESS_EFFECT_REVERSED'
        effects['recognized_fact_voided'] = pk
    elif row.entity_type == 'ENCAISSEMENT':
        cash = EncaissementTerrain.objects.select_for_update().get(pk=pk,submission=row)
        allocations = list(Lettrage.objects.select_for_update().filter(payment=cash.payment))
        total = sum((item.montant for item in allocations),Decimal('0.00'))
        if total != cash.montant_affecte:
            raise ValidationError('CASH_ALLOCATION_LEDGER_MISMATCH')
        if not total:
            raise ValidationError('NO_ALLOCATION_TO_REVERSE')
        for item in allocations:
            void(item,data['decision_uuid'])
        cash.montant_affecte = Decimal('0.00')
        cash.montant_a_rapprocher = cash.montant_recu
        cash.save(update_fields=['montant_affecte','montant_a_rapprocher'])
        outcome.business_status = 'NEEDS_RECONCILIATION'
        outcome.reason_code = 'CASH_ALLOCATION_REVERSED'
        effects = {'allocation_undone':format(total,'.2f'), 'physical_cash_unchanged':True,
            'payment_id':cash.payment_id,'montant_recu':format(cash.montant_recu,'.2f')}
    else:
        raise ValidationError('REVERSAL_KIND_UNSUPPORTED')
    return effects
