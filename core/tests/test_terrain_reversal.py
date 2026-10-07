from decimal import Decimal
from datetime import timedelta
import threading
from unittest import skipUnless
from django.db import connection, connections, transaction, DatabaseError
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient
from core.models import (Vente, Lettrage, Payment, EncaissementTerrain, Mouvement,
    TerrainSubmission, TerrainStockAdjustment, TerrainDecision, CollecteOeufs,
    AffectationMouvementOeufs, VenteOeufs)
from core.egg_services import sync_collection_stock_movement, get_dated_egg_stock
from core.tests.test_terrain_reconciliation import ReconciliationFixture


class TerrainReversalTests(ReconciliationFixture, TestCase):
    def test_sale_reversal_restores_stock_and_keeps_original_sale_and_movement(self):
        operation = self.sale()
        self.apply(operation)
        sale = Vente.objects.get()
        movement = sale.mouvement_animal
        data = self.decision(operation, 'REVERSE')
        result = self.submit_decision(operation, data)
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data['receipt']['business_status'], 'SUPERSEDED')
        self.assertEqual(result.data['receipt']['stock_snapshots'][0]['stock'], 20)
        self.assertEqual(self.lot.stock, 20)
        self.assertFalse(Vente.objects.exists())
        self.assertEqual(Vente.history.get(pk=sale.pk).quantite, 3)
        movement.refresh_from_db()
        self.assertEqual(movement.quantite_signee, -3)
        self.assertEqual(TerrainStockAdjustment.objects.get().signed_quantity, 3)
        self.assertEqual(self.submit_decision(operation, data).status_code, 200)
        self.assertEqual(TerrainStockAdjustment.objects.count(), 1)
        self.assertEqual(TerrainDecision.objects.count(), 1)
        self.assertEqual(TerrainSubmission.objects.get().payload, operation['payload'])
        repeat = self.submit_decision(operation, self.decision(operation, 'REVERSE', version=1))
        self.assertEqual(repeat.status_code, 400)

    def test_corrected_sale_reverses_effective_quantity_not_original_declaration(self):
        operation = self.sale(21)
        self.apply(operation)
        payload = dict(operation['payload'], quantite=2)
        self.assertEqual(self.submit_decision(operation,
            self.decision(operation, 'CORRECTION', payload=payload)).status_code, 200)
        result = self.submit_decision(operation, self.decision(operation, 'REVERSE', version=1))
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(TerrainStockAdjustment.objects.get().signed_quantity, 2)
        self.assertEqual(self.lot.stock, 20)
        self.assertEqual(TerrainSubmission.objects.get().payload['quantite'], 21)

    def test_paid_sale_requires_explicit_allocation_reversal_and_preserves_physical_cash(self):
        sale = self.sale()
        self.apply(sale)
        cash = self.terrain('ENCAISSEMENT', {'client_ref':{'server_id':self.customer.pk},
            'vente_ref':{'local_uuid':sale['local_entity_id']}, 'montant_recu':'50000.00',
            'mode':'ESPECES'}, 2, [sale['client_operation_id']])
        self.apply(cash)
        refused = self.submit_decision(sale, self.decision(sale, 'REVERSE'))
        self.assertEqual(refused.status_code, 400)
        self.assertFalse(TerrainStockAdjustment.objects.exists())
        data = self.decision(cash, 'REVERSE')
        result = self.submit_decision(cash, data)
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data['receipt']['business_status'], 'NEEDS_RECONCILIATION')
        recognized = EncaissementTerrain.objects.get()
        self.assertEqual(recognized.montant_recu, Decimal('50000.00'))
        self.assertEqual(recognized.montant_affecte, Decimal('0.00'))
        self.assertEqual(recognized.montant_a_rapprocher, Decimal('50000.00'))
        self.assertEqual(Payment.objects.get().montant, Decimal('50000.00'))
        self.assertFalse(Lettrage.objects.exists())
        self.assertEqual(Lettrage.history.get().montant, Decimal('30000.00'))
        self.assertEqual(Vente.objects.get().reste_a_payer, Decimal('30000.00'))
        self.assertEqual(self.submit_decision(cash, data).status_code, 200)
        self.assertEqual(self.submit_decision(sale, self.decision(sale, 'REVERSE')).status_code, 200)
        self.assertEqual(self.lot.stock, 20)
        self.assertEqual(Payment.objects.count(), 1)

    def test_removal_reversal_is_one_compensation_without_removing_original(self):
        operation = self.terrain('MORTALITE', {'lot_ref':self.lot_ref(), 'quantite':2})
        self.apply(operation)
        result = self.submit_decision(operation, self.decision(operation, 'REVERSE'))
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(self.lot.stock, 20)
        self.assertEqual(Mouvement.objects.get(type_mouvement='MORTALITE').quantite_signee, -2)

    def test_normal_delete_cannot_erase_terrain_sale_movement_or_cascading_lot(self):
        operation = self.sale(); self.apply(operation)
        sale = Vente.objects.get()
        with self.assertRaises(ValidationError): sale.delete()
        with self.assertRaises(ValidationError): Vente.objects.all().delete()
        with self.assertRaises(ValidationError): sale.mouvement_animal.delete()
        self.assertEqual(Vente.objects.count(), 1)
        self.assertEqual(self.lot.stock, 17)

    def test_cash_sale_picker_is_paginated_and_never_includes_another_client(self):
        self.apply(self.sale())
        cash = self.terrain('ENCAISSEMENT', {'client_ref':{'server_id':self.customer.pk},
            'montant_recu':'50000.00', 'mode':'ESPECES'}, 2)
        self.apply(cash)
        client = APIClient(); client.force_authenticate(self.owner)
        result = client.get('/api/offline/reconciliation/'+cash['client_operation_id']+'/cash-sales/')
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data['count'], 1)
        self.assertEqual(result.data['results'][0]['reste_a_payer'], '30000.00')

    def test_egg_reversal_returns_exact_fifo_roots_and_can_resell_without_erasing_allocations(self):
        self.lot.type_production = 'OEUFS'; self.lot.save()
        at = timezone.now() - timedelta(hours=2)
        roots = []
        for offset, quantity in [(2, 5), (1, 10)]:
            root = CollecteOeufs.objects.create(exploitation=self.farm, lot=self.lot,
                collecte_at=at-timedelta(hours=offset), nombre_collecte=quantity, created_by=self.jean)
            sync_collection_stock_movement(root); roots.append(root)
        def egg_sale(sequence, when):
            operation = self.terrain('VENTE_OEUFS', {'lot_ref':self.lot_ref(),
                'client_ref':{'server_id':self.customer.pk}, 'conditionnement':'UNITE',
                'nombre_conditionnements':12, 'prix_unitaire_conditionnement':'0.13'}, sequence)
            operation['business_occurred_at'] = when.isoformat()
            return operation
        operation = egg_sale(1, at)
        self.assertEqual(self.apply(operation)['business_status'], 'CONFIRMED')
        result = self.submit_decision(operation, self.decision(operation, 'REVERSE'))
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data['receipt']['stock_snapshots'][0]['stock_oeufs'], 15)
        self.assertEqual(list(TerrainStockAdjustment.objects.order_by('collection_id').values_list(
            'collection_id','signed_quantity')), [(roots[0].pk,5),(roots[1].pk,7)])
        self.assertEqual(AffectationMouvementOeufs.objects.count(), 2)
        self.assertEqual(VenteOeufs.history.count(), 1)
        self.assertFalse(VenteOeufs.objects.exists())
        backdated = egg_sale(2, at)
        self.assertEqual(self.apply(backdated)['business_status'], 'NEEDS_RECONCILIATION')
        for sequence in (3, 4):
            next_sale = egg_sale(sequence, timezone.now())
            self.assertEqual(self.apply(next_sale)['business_status'], 'CONFIRMED')
            self.assertEqual(self.submit_decision(next_sale,
                self.decision(next_sale, 'REVERSE')).status_code, 200)
        state = get_dated_egg_stock(self.farm, self.lot)
        self.assertTrue(state['origines_completes'])
        self.assertEqual(state['stock_global'], 15)
        self.assertEqual(AffectationMouvementOeufs.objects.count(), 6)

    @skipUnless(connection.vendor == 'postgresql', 'Real PostgreSQL trigger required')
    def test_stock_compensation_is_append_only_in_direct_sql(self):
        operation = self.sale()
        self.apply(operation)
        self.assertEqual(self.submit_decision(operation, self.decision(operation, 'REVERSE')).status_code, 200)
        pk = TerrainStockAdjustment.objects.get().pk
        for statement in ['UPDATE core_terrainstockadjustment SET signed_quantity=99 WHERE id=%s',
                          'DELETE FROM core_terrainstockadjustment WHERE id=%s']:
            with self.assertRaises(DatabaseError) as error:
                with transaction.atomic(), connection.cursor() as cursor:
                    cursor.execute(statement, [pk])
            self.assertEqual(getattr(error.exception.__cause__, 'sqlstate', None) or
                getattr(error.exception.__cause__, 'pgcode', None), 'P0001')


@skipUnless(connection.vendor == 'postgresql', 'Real PostgreSQL concurrency required')
class ConcurrentReversalTests(ReconciliationFixture, TransactionTestCase):
    def test_two_simultaneous_reversals_create_one_counter_and_one_stock_restoration(self):
        operation = self.sale(); self.apply(operation)
        barrier = threading.Barrier(2); results = []; errors = []
        def reverse():
            connections.close_all()
            try:
                barrier.wait(timeout=10)
                response = self.submit_decision(operation, self.decision(operation, 'REVERSE'))
                results.append(response.status_code)
            except Exception as error: errors.append(repr(error))
            finally: connections.close_all()
        threads = [threading.Thread(target=reverse) for _ in range(2)]
        for thread in threads: thread.start()
        for thread in threads: thread.join(timeout=20); self.assertFalse(thread.is_alive())
        self.assertEqual(errors, []); self.assertCountEqual(results, [200, 400])
        self.assertEqual(TerrainStockAdjustment.objects.count(), 1)
        self.assertEqual(TerrainDecision.objects.count(), 1)
        self.assertEqual(self.lot.stock, 20)
