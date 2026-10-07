from decimal import Decimal
from datetime import timedelta
import threading
import uuid
from unittest import skipUnless
from django.db import connection, connections, transaction, DatabaseError
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient
from core.models import (Vente, Lettrage, Payment, EncaissementTerrain, Mouvement,
    TerrainSubmission, TerrainStockAdjustment, TerrainDecision, CollecteOeufs,
    AffectationMouvementOeufs, VenteOeufs, Depense, ConsommationAliment, PeseeProduction, Achat, Lot, Client, Task, AuditEvent)
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

    def test_expense_with_active_feed_requires_explicit_dependency_reversal(self):
        expense = self.terrain('DEPENSE', {'lot_ref':self.lot_ref(),'montant':'123.45'})
        self.apply(expense)
        feed = self.terrain('ALIMENTATION', {'lot_ref':self.lot_ref(),'aliment':'Aliment test',
            'quantite_kg':'1.234','prix_kg':'10.00',
            'depense_ref':{'local_uuid':expense['local_entity_id']}},2,[expense['client_operation_id']])
        self.apply(feed)
        self.assertEqual(self.submit_decision(expense,self.decision(expense,'REVERSE')).status_code,400)
        self.assertEqual(Depense.objects.get().montant,Decimal('123.45'))
        self.assertEqual(self.submit_decision(feed,self.decision(feed,'REVERSE')).status_code,200)
        self.assertFalse(ConsommationAliment.objects.exists())
        self.assertEqual(ConsommationAliment.history.get().quantite_kg,Decimal('1.234'))
        self.assertEqual(Depense.objects.count(),1)
        self.assertEqual(self.submit_decision(expense,self.decision(expense,'REVERSE')).status_code,200)
        self.assertFalse(Depense.objects.exists())
        self.assertEqual(Depense.history.get().montant,Decimal('123.45'))
        self.assertEqual(self.lot.stock,20)
        self.assertFalse(TerrainStockAdjustment.objects.exists())

    def test_weighing_reversal_retains_original_measurement_and_excludes_it_from_current_series(self):
        operation=self.terrain('PESEE',{'lot_ref':self.lot_ref(),'nombre_animaux_peses':2,'poids_total_kg':'1.234'})
        self.apply(operation)
        result=self.submit_decision(operation,self.decision(operation,'REVERSE'))
        self.assertEqual(result.status_code,200,result.data)
        self.assertEqual(result.data['receipt']['business_status'],'SUPERSEDED')
        self.assertFalse(PeseeProduction.objects.exists())
        self.assertEqual(PeseeProduction.history.get().poids_total_kg,Decimal('1.234'))

    def test_current_financial_views_exclude_voided_facts_and_do_not_multiply_related_totals(self):
        reversed_sale=self.sale();self.apply(reversed_sale)
        self.assertEqual(self.submit_decision(reversed_sale,self.decision(reversed_sale,'REVERSE')).status_code,200)
        for sequence in (2,3): self.apply(self.sale(1,sequence))
        expense=self.terrain('DEPENSE',{'lot_ref':self.lot_ref(),'montant':'123.45'},4)
        self.apply(expense)
        self.assertEqual(self.submit_decision(expense,self.decision(expense,'REVERSE')).status_code,200)
        Depense.objects.create(lot=self.lot,montant=Decimal('10.00'))
        Depense.objects.create(lot=self.lot,montant=Decimal('10.00'))
        client=APIClient();client.force_authenticate(self.owner)
        result=client.get('/api/performance-lots/')
        self.assertEqual(result.status_code,200,result.data)
        self.assertEqual(Decimal(str(result.data[0]['marge'])),Decimal('19980.00'))

    def test_purchase_reversal_requires_reversed_removal_then_archives_original_lot_without_deleting_it(self):
        purchase=self.terrain('ACHAT',{'nom_lot':'Lot acheté','espece':self.species.pk,
            'quantite':10,'prix_total':'1250.00','prix_unitaire':'125.00'})
        self.apply(purchase);child=Achat.objects.get().lot
        removal=self.terrain('DON',{'lot_ref':{'local_uuid':purchase['local_entity_id']},'quantite':2},2,
            [purchase['client_operation_id']]);self.apply(removal)
        self.assertEqual(self.submit_decision(purchase,self.decision(purchase,'REVERSE')).status_code,400)
        self.assertEqual(self.submit_decision(removal,self.decision(removal,'REVERSE')).status_code,200)
        result=self.submit_decision(purchase,self.decision(purchase,'REVERSE'))
        self.assertEqual(result.status_code,200,result.data)
        child.refresh_from_db();self.assertEqual(child.stock,0);self.assertEqual(child.statut_production,'TERMINE')
        self.assertFalse(Achat.objects.exists());self.assertEqual(Achat.history.get().quantite,10)
        self.assertTrue(child.mouvements.filter(type_mouvement='ACHAT').exists())
        attempted=self.terrain('MORTALITE',{'lot_ref':{'server_id':child.pk},'quantite':1},3)
        self.assertEqual(self.apply(attempted)['reason_code'],'LOT_ORIGIN_REVERSED')
        self.assertEqual(self.lot.stock,20)

    def test_birth_reversal_compensates_only_living_child_without_changing_parent(self):
        birth=self.terrain('NAISSANCE',{'lot_ref':self.lot_ref(),'total_naissances':5,
            'mort_nes':2,'nom_nouveau_lot':'Enfant test'})
        self.apply(birth);movement=Mouvement.objects.get(type_mouvement='NAISSANCE')
        result=self.submit_decision(birth,self.decision(birth,'REVERSE'))
        self.assertEqual(result.status_code,200,result.data)
        self.assertEqual(movement.lot.stock,0);self.assertEqual(self.lot.stock,20)
        movement.refresh_from_db();self.assertEqual(movement.quantite,3);self.assertEqual(movement.mort_nes,2)
        self.assertEqual(TerrainStockAdjustment.objects.get().signed_quantity,-3)

    def test_collection_reversal_keeps_production_movement_and_removes_only_commercialisable_stock(self):
        self.lot.type_production='OEUFS';self.lot.save()
        collection=self.terrain('COLLECTE_OEUFS',{'lot_ref':self.lot_ref(),'nombre_collecte':12,
            'nombre_casses':2,'nombre_declasses':1,'nombre_consommes_donnes':1})
        self.apply(collection)
        result=self.submit_decision(collection,self.decision(collection,'REVERSE'))
        self.assertEqual(result.status_code,200,result.data)
        self.assertEqual(result.data['receipt']['stock_snapshots'][0]['stock_oeufs'],0)
        self.assertFalse(CollecteOeufs.objects.exists())
        self.assertEqual(CollecteOeufs.history.get().nombre_collecte,12)
        self.assertEqual(TerrainStockAdjustment.objects.get().signed_quantity,-8)
        self.assertTrue(get_dated_egg_stock(self.farm,self.lot)['origines_completes'])

    def test_client_archive_preserves_identity_and_refuses_active_sales_or_physical_payments(self):
        operation=self.terrain('CLIENT',{'nom':'Client reçu conservé'})
        self.apply(operation)
        customer=Client.objects.get(nom='Client reçu conservé')
        sale=self.sale(sequence=2);sale['payload']['client_ref']={'server_id':customer.pk};self.apply(sale)
        self.assertEqual(self.submit_decision(operation,self.decision(operation,'REVERSE')).status_code,400)
        self.assertEqual(self.submit_decision(sale,self.decision(sale,'REVERSE')).status_code,200)
        self.assertEqual(self.submit_decision(operation,self.decision(operation,'REVERSE')).status_code,200)
        self.assertFalse(Client.objects.filter(pk=customer.pk).exists())
        self.assertEqual(Client.history.get(pk=customer.pk).nom,'Client reçu conservé')

    def test_applied_task_correction_requires_current_version_and_keeps_owner_plan_and_original_report(self):
        task=Task.objects.create(exploitation=self.farm,title='Plan propriétaire',date=timezone.localdate(),
            assigned_to=self.jean,description='Organisation conservée')
        operation=self.terrain('TASK',{'task_id':task.pk,'status':'IN_PROGRESS','report':'Compte rendu original'})
        operation['operation_type']='UPDATE';operation['expected_server_version']='1';self.apply(operation)
        task.refresh_from_db();task.title='Plan révisé';task.version+=1;task.save()
        payload={'task_id':task.pk,'status':'TODO','report':'Correction motivée'}
        data=self.decision(operation,'CORRECTION',payload=payload);data['expected_entity_version']='2'
        self.assertEqual(self.submit_decision(operation,data).status_code,400)
        self.assertFalse(TerrainDecision.objects.exists())
        data['expected_entity_version']=str(task.version)
        result=self.submit_decision(operation,data)
        self.assertEqual(result.status_code,200,result.data)
        task.refresh_from_db();self.assertEqual(task.status,'TODO');self.assertEqual(task.report,'Correction motivée')
        self.assertEqual(result.data['receipt']['task_snapshot']['version'],task.version)
        self.assertEqual(result.data['receipt']['task_snapshot']['title'],'Plan révisé')
        self.assertEqual(result.data['receipt']['task_snapshot']['exploitation'],self.farm.pk)
        self.assertEqual(task.title,'Plan révisé');self.assertEqual(task.description,'Organisation conservée')
        self.assertEqual(task.assigned_to,self.jean)
        self.assertEqual(TerrainSubmission.objects.get().payload['report'],'Compte rendu original')
        self.assertTrue(AuditEvent.objects.filter(entity_type='core.task',action='UPDATE',
            actor_user_id=self.jean.pk,decision_actor_id=self.owner.pk).exists())

    def test_decision_history_is_bounded_and_paginated_without_dropping_original(self):
        operation=self.sale(21);self.apply(operation)
        self.assertEqual(self.submit_decision(operation,self.decision(operation,'CANCEL')).status_code,200)
        row=TerrainSubmission.objects.get()
        for _ in range(50):
            TerrainDecision.objects.create(exploitation=self.farm,submission=row,
                decision_uuid=uuid.uuid4(),decision_actor_id=self.owner.pk,action='CANCEL',
                reason='Décision historique conservée pour vérification.',request_digest='a'*64)
        client=APIClient();client.force_authenticate(self.owner)
        path='/api/offline/reconciliation/'+operation['client_operation_id']+'/'
        first=client.get(path);second=client.get(path,{'decision_page':2})
        self.assertEqual(first.status_code,200);self.assertEqual(second.status_code,200)
        self.assertEqual(first.data['decisions_count'],51);self.assertEqual(len(first.data['decisions']),50)
        self.assertEqual(first.data['decisions_next_page'],2);self.assertEqual(len(second.data['decisions']),1)
        self.assertIsNone(second.data['decisions_next_page'])
        self.assertEqual(second.data['original_payload']['quantite'],21)

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
