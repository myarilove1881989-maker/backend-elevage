from datetime import timedelta
from decimal import Decimal
import copy
import threading
from unittest import skipUnless
from django.db import connection, connections, transaction, DatabaseError
from django.core.exceptions import ValidationError
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient
from core.models import (Client,Vente,VenteOeufs,Payment,Lettrage,EncaissementTerrain,User,Espece,Lot,
    CollecteOeufs,MouvementOeufs,AffectationMouvementOeufs,TerrainSubmission,AuditEvent)
from core.tests.test_terrain_operations import OperationFixture
from core.egg_services import sync_collection_stock_movement


class TerrainSalesTests(OperationFixture,TestCase):
    def setUp(self):
        super().setUp()
        self.client=Client.objects.create(exploitation=self.farm,nom='Client synthétique')

    def sale(self,quantity=3,price='10000.00',sequence=1):
        return self.terrain('VENTE_ANIMAUX',{'lot_ref':self.lot_ref(),
            'client_ref':{'server_id':self.client.pk},'quantite':quantity,'prix_unitaire':price},sequence)

    def cash(self,sale,amount='50000.00',sequence=2):
        return self.terrain('ENCAISSEMENT',{'client_ref':{'server_id':self.client.pk},
            'vente_ref':{'local_uuid':sale['local_entity_id']},'montant_recu':amount,
            'mode':'ESPECES'},sequence,[sale['client_operation_id']])

    def test_animal_sale_and_lost_response_apply_once_keep_author(self):
        sale=self.sale();first=self.apply(sale);self.apply(sale)
        self.assertEqual(first['business_status'],'CONFIRMED')
        self.assertEqual(self.lot.stock,17)
        self.assertEqual(Vente.objects.count(),1)
        self.assertEqual(Vente.objects.get().created_by,self.jean)
        self.assertEqual(Vente.objects.get().montant_total,Decimal('30000.00'))
        self.assertEqual(AuditEvent.objects.filter(action='TERRAIN_APPLIED').count(),1)

    def test_oversale_retains_original_without_negative_stock(self):
        operation=self.sale(21);operation['payload']['note']='Vente physique, inventaire à vérifier.'
        receipt=self.apply(operation)
        self.assertEqual(receipt['business_status'],'NEEDS_RECONCILIATION')
        self.assertEqual(receipt['reason_code'],'STOCK_INSUFFICIENT')
        self.assertEqual(TerrainSubmission.objects.get().payload,operation['payload'])
        self.assertFalse(Vente.objects.exists());self.assertEqual(self.lot.stock,20)

    def test_animal_sale_note_and_client_are_kept_on_stock_movement(self):
        operation=self.sale();operation['payload']['note']='Constat terrain'
        self.apply(operation)
        movement=self.lot.mouvements.get(type_mouvement='VENTE')
        self.assertEqual(movement.note,'Constat terrain');self.assertEqual(movement.client,self.client)

    def test_egg_quantity_and_amount_overflow_are_retained_for_review_instead_of_retrying_forever(self):
        self.lot.type_production='OEUFS';self.lot.save()
        operation=self.egg_sale(timezone.now(),2000000000)
        operation['payload'].update(conditionnement='CARTON',oeufs_par_conditionnement=30)
        self.assertEqual(self.apply(operation)['reason_code'],'QUANTITY_OUT_OF_RANGE')
        second=self.egg_sale(timezone.now(),1000000);second['local_sequence']=2
        second['payload']['prix_unitaire_conditionnement']='99999999.99'
        self.assertEqual(self.apply(second)['reason_code'],'AMOUNT_OUT_OF_RANGE')
        self.assertEqual(TerrainSubmission.objects.count(),2);self.assertFalse(VenteOeufs.objects.exists())

    def test_cash_50000_allocates_30000_and_preserves_20000_for_review(self):
        sale=self.sale();self.apply(sale);cash=self.cash(sale)
        receipt=self.apply(cash);self.apply(cash)
        self.assertEqual(receipt['business_status'],'NEEDS_RECONCILIATION')
        self.assertEqual(receipt['cash_recognition']['montant_recu'],'50000.00')
        recognized=EncaissementTerrain.objects.get()
        self.assertEqual(recognized.montant_recu,Decimal('50000.00'))
        self.assertEqual(recognized.montant_affecte,Decimal('30000.00'))
        self.assertEqual(recognized.montant_a_rapprocher,Decimal('20000.00'))
        self.assertEqual(Payment.objects.get().montant,Decimal('50000.00'))
        self.assertEqual(Lettrage.objects.get().montant,Decimal('30000.00'))
        self.assertEqual(Vente.objects.get().reste_a_payer,Decimal('0.00'))
        self.assertEqual(recognized.created_by,self.jean)
        self.assertEqual(Payment.objects.count(),1);self.assertEqual(Lettrage.objects.count(),1)

    def test_cash_physical_amount_cannot_be_rewritten_while_allocation_can_evolve(self):
        sale=self.sale();self.apply(sale);self.apply(self.cash(sale))
        cash=EncaissementTerrain.objects.get()
        cash.montant_recu=Decimal('30000.00')
        with self.assertRaises(ValidationError):cash.save()
        cash.refresh_from_db()
        with self.assertRaises(ValidationError):cash.delete()
        cash.montant_affecte=Decimal('40000.00');cash.montant_a_rapprocher=Decimal('10000.00');cash.save()
        cash.refresh_from_db();self.assertEqual(cash.montant_recu,Decimal('50000.00'))

    @skipUnless(connection.vendor=='postgresql','Real PostgreSQL cash trigger required')
    def test_postgresql_refuses_cash_origin_update_and_delete_with_raw_sql(self):
        sale=self.sale();self.apply(sale);self.apply(self.cash(sale))
        pk=EncaissementTerrain.objects.get().pk
        for statement in [
            'UPDATE core_encaissementterrain SET montant_recu=30000,montant_a_rapprocher=0 WHERE id=%s',
            'DELETE FROM core_encaissementterrain WHERE id=%s',
        ]:
            with self.assertRaises(DatabaseError) as error:
                with transaction.atomic(),connection.cursor() as cursor:cursor.execute(statement,[pk])
            self.assertEqual(getattr(error.exception.__cause__,'sqlstate',None) or getattr(error.exception.__cause__,'pgcode',None),'P0001')
        self.assertEqual(EncaissementTerrain.objects.get().montant_recu,Decimal('50000.00'))

    def test_exact_cents_and_dependency_client_sale_cash_reverse_order(self):
        client=self.terrain('CLIENT',{'nom':'Client local'},1)
        sale=self.sale(3,'13.01',2)
        sale['payload']['client_ref']={'local_uuid':client['local_entity_id']}
        sale['dependencies']=[client['client_operation_id']]
        cash=self.cash(sale,'39.03',3)
        cash['payload']['client_ref']={'local_uuid':client['local_entity_id']}
        cash['dependencies'].append(client['client_operation_id'])
        self.assertEqual(self.apply(cash)['business_status'],'WAITING_DEPENDENCY')
        self.apply(sale);self.apply(client)
        self.assertEqual(self.apply(cash)['business_status'],'CONFIRMED')
        self.assertEqual(Payment.objects.get().montant,Decimal('39.03'))
        self.assertEqual(Lettrage.objects.get().montant,Decimal('39.03'))
        self.assertEqual(Vente.objects.get().reste_a_payer,Decimal('0.00'))

    def test_cash_without_target_recognizes_full_amount_without_automatic_allocation(self):
        self.apply(self.sale())
        operation=self.terrain('ENCAISSEMENT',{'client_ref':{'server_id':self.client.pk},
            'montant_recu':'50000.00','mode':'MOBILE_MONEY'},2)
        self.assertEqual(self.apply(operation)['business_status'],'NEEDS_RECONCILIATION')
        self.assertEqual(EncaissementTerrain.objects.get().montant_a_rapprocher,Decimal('50000.00'))
        self.assertFalse(Lettrage.objects.exists())

    def test_cash_cannot_target_other_clients_sale(self):
        sale=self.sale();self.apply(sale)
        cash=self.cash(sale)
        other=Client.objects.create(exploitation=self.farm,nom='Autre')
        cash['payload']['client_ref']={'server_id':other.pk}
        self.assertEqual(self.apply(cash)['business_status'],'NEEDS_RECONCILIATION')
        self.assertFalse(Payment.objects.exists());self.assertEqual(TerrainSubmission.objects.count(),2)

    def test_sales_cache_has_decimal_debt_and_scoped_reference_after_partial_cash(self):
        sale=self.sale();self.apply(sale);self.apply(self.cash(sale,'13.01'))
        owner=User.objects.create_user(username='sales-cache-other-farm')
        species=Espece.objects.create(exploitation=owner.exploitation,nom='Autre')
        lot=Lot.objects.create(exploitation=owner.exploitation,espece=species,nom='Autre',date_debut=timezone.localdate())
        outsider=Client.objects.create(exploitation=owner.exploitation,nom='Client autre ferme')
        Vente.objects.create(lot=lot,client=outsider,quantite=1,prix_unitaire=Decimal('1.00'))
        client=APIClient();client.force_authenticate(self.jean)
        response=client.get('/api/cache-page/',{'collection':'sales'})
        self.assertEqual(response.status_code,200,response.data)
        self.assertEqual(len(response.data['results']),1)
        row=response.data['results'][0]
        self.assertEqual(row['reste_a_payer'],'29986.99')
        self.assertEqual(row['montant_total'],'30000.00')
        self.assertEqual(row['entity_type'],'VENTE_ANIMAUX')
        other=Client.objects.create(exploitation=self.farm,nom='Client indépendant')
        self.assertNotEqual(row['client_id'],other.pk)

    def collect(self,at,quantity):
        self.lot.type_production='OEUFS';self.lot.save()
        collection=CollecteOeufs.objects.create(exploitation=self.farm,lot=self.lot,
            collecte_at=at,nombre_collecte=quantity,created_by=self.jean)
        sync_collection_stock_movement(collection)
        return collection

    def egg_sale(self,at,quantity=12):
        operation=self.terrain('VENTE_OEUFS',{'lot_ref':self.lot_ref(),
            'client_ref':{'server_id':self.client.pk},'conditionnement':'UNITE',
            'nombre_conditionnements':quantity,'prix_unitaire_conditionnement':'0.13'})
        operation['business_occurred_at']=at.isoformat()
        return operation

    def test_fifo_never_consumes_next_day_collection_even_when_synchronized_later(self):
        wednesday=timezone.now()-timedelta(days=2)
        self.collect(wednesday-timedelta(days=1),6)
        self.collect(wednesday+timedelta(days=1),30)
        operation=self.egg_sale(wednesday,12)
        receipt=self.apply(operation)
        self.assertEqual(receipt['business_status'],'NEEDS_RECONCILIATION')
        self.assertFalse(VenteOeufs.objects.exists());self.assertFalse(AffectationMouvementOeufs.objects.exists())
        self.assertEqual(MouvementOeufs.objects.count(),2)

    def test_composed_egg_sale_preserves_existing_packaging_and_note(self):
        at=timezone.now()-timedelta(hours=1);self.collect(at-timedelta(hours=1),100)
        operation=self.terrain('VENTE_OEUFS',{'lot_ref':self.lot_ref(),'client_ref':{'server_id':self.client.pk},
            'conditionnement':'COMPOSE','nombre_alveoles':2,'oeufs_supplementaires':5,'prix_total':'50.01','note':'Vente mixte terrain'})
        operation['business_occurred_at']=at.isoformat()
        self.assertEqual(self.apply(operation)['business_status'],'CONFIRMED')
        sale=VenteOeufs.objects.get()
        self.assertEqual(sale.nombre_oeufs,65);self.assertEqual(sale.montant_total,Decimal('50.01'))
        self.assertIn('Vente mixte terrain',sale.mouvement_stock.note)

    def test_fifo_excludes_later_same_day_and_uses_oldest_eligible_origins(self):
        at=timezone.now()-timedelta(days=1)
        first=self.collect(at-timedelta(hours=2),5)
        second=self.collect(at-timedelta(hours=1),10)
        future=self.collect(at+timedelta(minutes=1),30)
        operation=self.egg_sale(at)
        self.assertEqual(self.apply(operation)['business_status'],'CONFIRMED')
        self.apply(operation)
        self.assertEqual(VenteOeufs.objects.count(),1)
        self.assertEqual(list(AffectationMouvementOeufs.objects.order_by('collecte_id').values_list('collecte_id','quantite')),
            [(first.pk,5),(second.pk,7)])
        self.assertFalse(AffectationMouvementOeufs.objects.filter(collecte=future).exists())
        self.assertEqual(VenteOeufs.objects.get().mouvement_stock.date,at)
        self.assertEqual(VenteOeufs.objects.get().created_by,self.jean)


@skipUnless(connection.vendor=='postgresql','Real PostgreSQL sales/payment locks required')
class TerrainSalesConcurrencyTests(OperationFixture,TransactionTestCase):
    def setUp(self):
        super().setUp()
        self.client=Client.objects.create(exploitation=self.farm,nom='Client concurrence')

    def simultaneous(self,operations):
        barrier=threading.Barrier(2);results=[];errors=[]
        def worker(operation):
            connections.close_all()
            try:
                barrier.wait(timeout=10)
                response,_=self.send(copy.deepcopy(operation));results.append(response)
            except Exception as error:errors.append(error)
            finally:connections.close_all()
        threads=[threading.Thread(target=worker,args=(operation,)) for operation in operations]
        for thread in threads:thread.start()
        for thread in threads:thread.join(timeout=20)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertFalse(errors,errors);self.assertEqual(len(results),2)
        self.assertTrue(all(result.status_code==200 for result in results),results)

    def test_simultaneous_sales_never_confirm_negative_animal_stock(self):
        operations=[self.terrain('VENTE_ANIMAUX',{'lot_ref':self.lot_ref(),
            'client_ref':{'server_id':self.client.pk},'quantite':15,'prix_unitaire':'1.00'},seq) for seq in [1,2]]
        self.simultaneous(operations)
        self.assertEqual(self.lot.stock,5);self.assertEqual(Vente.objects.count(),1)
        self.assertEqual(TerrainSubmission.objects.count(),2)
        self.assertCountEqual(TerrainSubmission.objects.values_list('outcome__business_status',flat=True),
            ['CONFIRMED','NEEDS_RECONCILIATION'])

    def test_simultaneous_cash_receipts_never_overallocate_debt_or_lose_cash(self):
        sale=self.terrain('VENTE_ANIMAUX',{'lot_ref':self.lot_ref(),
            'client_ref':{'server_id':self.client.pk},'quantite':3,'prix_unitaire':'10000.00'})
        self.apply(sale)
        operations=[self.terrain('ENCAISSEMENT',{'client_ref':{'server_id':self.client.pk},
            'vente_ref':{'server_id':Vente.objects.get().pk},'montant_recu':'30000.00','mode':'ESPECES'},seq) for seq in [2,3]]
        self.simultaneous(operations)
        self.assertEqual(EncaissementTerrain.objects.count(),2);self.assertEqual(Payment.objects.count(),2)
        self.assertEqual(sum(EncaissementTerrain.objects.values_list('montant_recu',flat=True)),Decimal('60000.00'))
        self.assertEqual(sum(EncaissementTerrain.objects.values_list('montant_a_rapprocher',flat=True)),Decimal('30000.00'))
        self.assertEqual(sum(Lettrage.objects.values_list('montant',flat=True)),Decimal('30000.00'))
        self.assertEqual(Vente.objects.get().reste_a_payer,Decimal('0.00'))


class TerrainMoneyMigrationTests(OperationFixture,TransactionTestCase):
    previous=[('core','0022_exploitation_business_revision_and_more')]
    target=[('core','0023_alter_lettrage_montant_alter_payment_montant_and_more')]

    def setUp(self):
        super().setUp()
        self.client=Client.objects.create(exploitation=self.farm,nom='Client migration')
        executor=MigrationExecutor(connection);executor.migrate(self.previous)
        self.old=executor.loader.project_state(self.previous).apps.get_model('core','Payment')

    def tearDown(self):
        # Failed guards must leave legacy amounts unchanged; remove only the
        # synthetic test rows before restoring the full schema for other tests.
        self.old.objects.all().delete()
        executor=MigrationExecutor(connection);executor.migrate(executor.loader.graph.leaf_nodes('core'))
        super().tearDown()

    def legacy(self,amount):
        return self.old.objects.create(exploitation_id=self.farm.pk,client_id=self.client.pk,
            montant=amount,date=timezone.localdate())

    def test_valid_legacy_cents_are_preserved_by_actual_migration(self):
        payment=self.legacy(13.01)
        MigrationExecutor(connection).migrate(self.target)
        self.assertEqual(Payment.objects.get(pk=payment.pk).montant,Decimal('13.01'))

    def test_non_cent_legacy_amount_blocks_migration_without_truncation(self):
        payment=self.legacy(13.011)
        with self.assertRaisesMessage(RuntimeError,'explicit reconciliation'):
            MigrationExecutor(connection).migrate(self.target)
        self.assertEqual(self.old.objects.get(pk=payment.pk).montant,13.011)
