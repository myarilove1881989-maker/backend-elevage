from datetime import timedelta
from decimal import Decimal
from uuid import uuid4
from django.core.exceptions import ValidationError
from django.db import connection, transaction, DatabaseError
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied
from core.models import User, Espece, Lot, Depense, AuditEvent, Mouvement, Achat
from core.supply_models import (SupplyProduct, SupplyOperation, SupplyReceipt,
    SupplyMovement, SupplyVial, SupplyVialOpening)
from core.supply_services import create_product, configure_product, execute, stock, bags_to_kg


class SupplyFoundationTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username='supply-owner')
        self.other = User.objects.create_user(username='supply-other')
        self.farm = self.owner.exploitation
        self.species = Espece.objects.create(exploitation=self.farm, nom='Poulet test')
        self.lot = Lot.objects.create(exploitation=self.farm, espece=self.species,
            nom='Lot test', date_debut=timezone.localdate()-timedelta(days=20))
        self.feed = create_product(self.owner, self.farm.pk, kind='FEED', name='Croissance', alert_threshold='50')
        self.vaccine = create_product(self.owner, self.farm.pk, kind='VACCINE', name='Vaccin test')

    def command(self, kind, payload, **kwargs):
        return execute(self.owner, self.farm.pk, kwargs.pop('uuid', uuid4()), kind, payload, **kwargs)

    def feed_receipt(self, **extra):
        return self.command('FEED_RECEIPT', {'product':str(self.feed.pk), 'species':self.species.pk, 'kg':'500', **extra})

    def vaccine_receipt(self, **extra):
        return self.command('VACCINE_RECEIPT', {'product':str(self.vaccine.pk), 'species':self.species.pk,
            'manufacturer_batch':'FAB-TEST-001','expires_on':str(timezone.localdate()+timedelta(days=90)),
            'vial_count':10,'doses_per_vial':20, **extra})

    def balance(self, product=None, **kwargs):
        return stock(self.owner, self.farm.pk, (product or self.feed).pk, **kwargs)

    def test_500_80_420_and_retries(self):
        identifier = uuid4()
        p = {'product':str(self.feed.pk),'species':self.species.pk,'kg':'500'}
        first = self.command('FEED_RECEIPT',p,uuid=identifier)
        self.assertEqual(first.pk,self.command('FEED_RECEIPT',p,uuid=identifier).pk)
        out = {'product':str(self.feed.pk),'species':self.species.pk,'lot':self.lot.pk,'quantity':'80'}
        out_id = uuid4()
        self.command('DISTRIBUTE',out,uuid=out_id)
        self.command('DISTRIBUTE',out,uuid=out_id)
        self.assertEqual(self.balance()['physical'],Decimal('420'))
        self.assertEqual(SupplyReceipt.objects.count(),1)
        self.assertEqual(SupplyMovement.objects.count(),2)

    def test_uuid_changed_payload_rejected(self):
        identifier = uuid4()
        p = {'product':str(self.feed.pk),'species':self.species.pk,'kg':'500'}
        self.command('FEED_RECEIPT',p,uuid=identifier)
        with self.assertRaisesMessage(ValidationError,'SUPPLY_UUID_PAYLOAD_CONFLICT'):
            self.command('FEED_RECEIPT',{**p,'kg':'501'},uuid=identifier)
        self.assertEqual(self.balance()['physical'],500)

    def test_insufficient_stock_rolls_back_entire_command(self):
        self.feed_receipt()
        with self.assertRaisesMessage(ValidationError,'SUPPLY_STOCK_INSUFFICIENT'):
            self.command('DISTRIBUTE',{'product':str(self.feed.pk),'species':self.species.pk,
                'lot':self.lot.pk,'quantity':'501'})
        self.assertEqual(self.balance()['physical'],500)
        self.assertEqual(SupplyOperation.objects.count(),1)

    def test_explicit_bags_and_precision(self):
        self.assertEqual(bags_to_kg('10','50'),500)
        self.feed_receipt(kg='500')
        for args in [('1.5','50'),('10','0'),('10',50.0),('NaN','50')]:
            with self.assertRaises(ValidationError):bags_to_kg(*args)
        self.command('FEED_RECEIPT',{'product':str(self.feed.pk),'species':self.species.pk,'bags':'2','kg_per_bag':'25'})
        self.assertEqual(self.balance()['physical'],550)
        with self.assertRaises(ValidationError):self.feed_receipt(bags='10',kg_per_bag='50')

    def test_transfer_conserves_global_stock_and_reservations(self):
        self.feed_receipt()
        self.command('TRANSFER',{'product':str(self.feed.pk),'species':self.species.pk,
            'target_lot':self.lot.pk,'quantity':'100'})
        self.assertEqual(self.balance()['physical'],500)
        self.assertEqual(self.balance(lot_id=self.lot.pk)['physical'],100)
        self.command('DISTRIBUTE',{'product':str(self.feed.pk),'species':self.species.pk,
            'source_lot':self.lot.pk,'lot':self.lot.pk,'quantity':'80'})
        self.assertEqual(self.balance()['physical'],420)
        self.assertEqual(self.balance(lot_id=self.lot.pk)['physical'],20)

    def test_failed_transfer_is_atomic(self):
        self.feed_receipt()
        with self.assertRaises(ValidationError):
            self.command('TRANSFER',{'product':str(self.feed.pk),'species':self.species.pk,
                'target_lot':self.lot.pk,'quantity':'501'})
        self.assertEqual(SupplyMovement.objects.count(),1)

    def test_corrections_and_annulment_preserve_originals(self):
        receipt = self.feed_receipt()
        loss = self.command('LOSS',{'product':str(self.feed.pk),'species':self.species.pk,'quantity':'10'},reason='Sac abîmé')
        self.command('REVERSE',{'original':loss.pk},reason='Erreur de saisie constatée')
        self.assertEqual(self.balance()['physical'],500)
        self.assertEqual(SupplyMovement.objects.get(operation=loss).quantity,-10)
        self.command('CORRECT',{'product':str(self.feed.pk),'species':self.species.pk,'quantity':'-5'},reason='Inventaire')
        self.assertEqual(self.balance()['physical'],495)
        with self.assertRaises(ValidationError):self.command('REVERSE',{'original':receipt.pk},reason='Achat annulé')
        with self.assertRaises(ValidationError):self.command('REVERSE',{'original':loss.pk},reason='Doublon')
        self.assertEqual(SupplyReceipt.objects.count(),1)
        self.assertTrue(AuditEvent.objects.filter(reason_code='SUPPLY_CORRECT').exists())

    def test_full_receipt_cancellation_and_idempotence(self):
        receipt = self.feed_receipt()
        identifier = uuid4()
        first=self.command('REVERSE',{'original':receipt.pk},reason='Annulation',uuid=identifier)
        self.assertEqual(first.pk,self.command('REVERSE',{'original':receipt.pk},reason='Annulation',uuid=identifier).pk)
        self.assertEqual(self.balance()['physical'],0)
        self.assertEqual(SupplyReceipt.objects.count(),1)

    def test_ten_vials_twenty_doses(self):
        receipt = self.vaccine_receipt()
        self.assertEqual(SupplyVial.objects.count(),10)
        self.assertEqual(SupplyVial.objects.values('pk').distinct().count(),10)
        self.assertEqual(self.balance(self.vaccine)['physical'],200)
        self.assertEqual(self.balance(self.vaccine)['usable'],200)
        self.assertEqual(SupplyReceipt.objects.get(operation=receipt).quantity,200)

    def test_expired_vials_physical_but_not_usable(self):
        self.vaccine_receipt(expires_on=str(timezone.localdate()-timedelta(days=1)))
        self.assertEqual(self.balance(self.vaccine)['physical'],200)
        self.assertEqual(self.balance(self.vaccine)['usable'],0)
        vial = SupplyVial.objects.first()
        with self.assertRaises(ValidationError):self.command('OPEN',{'vial':str(vial.pk)})

    def test_opening_expiry_administer_loss_and_capacity(self):
        self.vaccine_receipt()
        vial=SupplyVial.objects.first()
        opened=timezone.now()-timedelta(hours=2)
        # Use a past receipt occurrence for a real opening chronology.
        with self.assertRaises(ValidationError):
            self.command('OPEN',{'vial':str(vial.pk),'opened_at':opened.isoformat(),'usable_seconds':3600})
        self.command('OPEN',{'vial':str(vial.pk),'usable_seconds':3600})
        self.command('ADMINISTER',{'product':str(self.vaccine.pk),'species':self.species.pk,
            'lot':self.lot.pk,'vial':str(vial.pk),'quantity':'5'})
        self.command('LOSS',{'product':str(self.vaccine.pk),'species':self.species.pk,
            'vial':str(vial.pk),'quantity':'2'},reason='Reliquat perdu')
        self.assertEqual(self.balance(self.vaccine)['physical'],193)
        self.assertEqual(self.balance(self.vaccine,at=timezone.now()+timedelta(hours=2))['usable'],180)
        with self.assertRaises(ValidationError):
            self.command('CORRECT',{'product':str(self.vaccine.pk),'species':self.species.pk,
                'vial':str(vial.pk),'quantity':'8'},reason='Inventaire')
        with self.assertRaises(ValidationError):self.command('OPEN',{'vial':str(vial.pk)})

    def test_unopened_and_fractional_administration_refused(self):
        self.vaccine_receipt()
        p={'product':str(self.vaccine.pk),'species':self.species.pk,'lot':self.lot.pk,
            'vial':str(SupplyVial.objects.first().pk),'quantity':'1'}
        with self.assertRaises(ValidationError):self.command('ADMINISTER',p)
        self.command('OPEN',{'vial':p['vial']})
        with self.assertRaises(ValidationError):self.command('ADMINISTER',{**p,'quantity':'0.5'})
        with self.assertRaises(ValidationError):self.command('ADMINISTER',{**p,'quantity':'21'})

    def test_expense_finance_and_animal_purchases_unchanged(self):
        expense=Depense.objects.create(lot=self.lot,montant='12345.67',note='Achat existant')
        before=list(Depense.objects.values())
        self.feed_receipt(expense=expense.pk)
        self.assertEqual(list(Depense.objects.values()),before)
        self.assertEqual(Achat.objects.count(),0)
        self.assertEqual(Mouvement.objects.count(),0)
        with self.assertRaises(ValidationError):self.feed_receipt(expense=expense.pk)

    def test_multi_farm_all_references_and_reads(self):
        with self.assertRaises(PermissionDenied):stock(self.other,self.farm.pk,self.feed.pk)
        with self.assertRaises(PermissionDenied):execute(self.other,self.farm.pk,uuid4(),'FEED_RECEIPT',{})
        foreign=Espece.objects.create(exploitation=self.other.exploitation,nom='Étranger')
        with self.assertRaises(ValidationError):self.feed_receipt(species=foreign.pk)
        expense=Depense.objects.create(lot=Lot.objects.create(exploitation=self.other.exploitation,
            espece=foreign,nom='Étranger',date_debut=timezone.localdate()),montant=10)
        with self.assertRaises(ValidationError):self.feed_receipt(expense=expense.pk)
        with self.assertRaises(ValidationError):stock(self.owner,self.farm.pk,self.feed.pk,lot_id=expense.lot_id)

    def test_operator_has_no_new_implicit_rights(self):
        from core.tests.functional_fixtures import operator_for_tests
        operator=operator_for_tests(self.owner,'supply-operator',['feed.create','expenses.create'])
        with self.assertRaises(PermissionDenied):stock(operator,self.farm.pk,self.feed.pk)
        with self.assertRaises(PermissionDenied):execute(operator,self.farm.pk,uuid4(),'FEED_RECEIPT',{})

    def test_closed_lots_and_wrong_species(self):
        self.feed_receipt()
        self.lot.date_fin=timezone.localdate();self.lot.save()
        with self.assertRaises(Exception) as result:
            self.command('DISTRIBUTE',{'product':str(self.feed.pk),'species':self.species.pk,'lot':self.lot.pk,'quantity':'1'})
        self.assertIn('LOT_CLOSED',str(result.exception))
        species=Espece.objects.create(exploitation=self.farm,nom='Canard')
        with self.assertRaises(ValidationError):self.feed_receipt(species=species.pk,lot=self.lot.pk)

    def test_immutable_history_and_stable_identity(self):
        self.feed_receipt()
        movement=SupplyMovement.objects.first()
        with self.assertRaises(ValidationError):movement.delete()
        movement.quantity=1
        with self.assertRaises(ValidationError):movement.save()
        with self.assertRaises(ValidationError):SupplyMovement.objects.all().update(quantity=1)
        original=self.feed.pk;self.feed.name='Nouveau nom';self.feed.save()
        self.assertEqual(self.feed.pk,original);self.assertEqual(self.balance()['physical'],500)
        self.feed.exploitation=self.other.exploitation
        with self.assertRaises(ValidationError):self.feed.save()

    def test_expense_cancellation_and_maintenance_blocked(self):
        from rest_framework.test import APIClient
        expense=Depense.objects.create(lot=self.lot,montant=100)
        self.feed_receipt(expense=expense.pk)
        api=APIClient();api.force_authenticate(self.owner)
        api.credentials(HTTP_ACCEPT='application/json; elevage-contract=2')
        response=api.post(f'/api/depenses/cancel/{expense.pk}/',{'operation_uuid':str(uuid4()),'reason':'Annulation'},format='json')
        self.assertEqual(response.status_code,400,response.data)
        self.farm.refresh_from_db()
        response=api.patch(f'/api/maintenance/expenses/{expense.pk}/',{'farm':self.farm.pk,
            'expected_revision':self.farm.business_revision,'confirm':True,'reason':'Correction',
            'values':{'montant':'90'}},format='json')
        self.assertEqual(response.status_code,400,response.data)
        expense.refresh_from_db();self.assertEqual(expense.montant,100)

    def test_database_history_protection(self):
        self.feed_receipt()
        if connection.vendor != 'postgresql':self.skipTest('PostgreSQL trigger qualification')
        for table in ['core_supplymovement','core_supplyoperation','core_supplyreceipt']:
            with self.assertRaises(DatabaseError), transaction.atomic():
                with connection.cursor() as cursor:cursor.execute(f'DELETE FROM {table}')

    def test_configurable_threshold_archive_and_retry(self):
        receipt=self.feed_receipt()
        configure_product(self.owner,self.farm.pk,self.feed.pk,name='Renommé',alert_threshold='600',archived=True)
        self.assertTrue(self.balance()['below_threshold'])
        with self.assertRaises(ValidationError):self.feed_receipt()
        self.assertEqual(execute(self.owner,self.farm.pk,receipt.operation_uuid,'FEED_RECEIPT',receipt.payload).pk,receipt.pk)
        self.assertEqual(self.balance()['physical'],500)

    def test_wrong_vial_and_species_refused(self):
        self.vaccine_receipt()
        vial=SupplyVial.objects.first()
        species=Espece.objects.create(exploitation=self.farm,nom='Autre espèce')
        with self.assertRaises(ValidationError):
            self.command('LOSS',{'product':str(self.vaccine.pk),'species':species.pk,
                'vial':str(vial.pk),'quantity':'1'},reason='Test')
        foreign=create_product(self.other,self.other.exploitation_id,kind='VACCINE',name='Étranger')
        with self.assertRaises(ValidationError):
            self.command('LOSS',{'product':str(foreign.pk),'species':self.species.pk,
                'vial':str(vial.pk),'quantity':'1'},reason='Test')

    def test_opened_unusable_vial_cannot_be_administered(self):
        from unittest.mock import patch
        self.vaccine_receipt()
        vial=SupplyVial.objects.first()
        self.command('OPEN',{'vial':str(vial.pk),'usable_seconds':60})
        later=timezone.now()+timedelta(minutes=2)
        with patch('core.supply_services.timezone.now',return_value=later):
            with self.assertRaisesMessage(ValidationError,'SUPPLY_VIAL_UNUSABLE'):
                self.command('ADMINISTER',{'product':str(self.vaccine.pk),'species':self.species.pk,
                    'lot':self.lot.pk,'vial':str(vial.pk),'quantity':'1'})

    def test_legacy_routes_do_not_create_supply_facts(self):
        from rest_framework.test import APIClient
        api=APIClient();api.force_authenticate(self.owner)
        api.credentials(HTTP_ACCEPT='application/json; elevage-contract=2')
        result=api.post('/api/alimentation/distributions/',{'lot':self.lot.pk,'aliment':'Ancien aliment',
            'quantite_kg':'80'},format='json')
        self.assertEqual(result.status_code,201,result.data)
        self.assertEqual(SupplyMovement.objects.count(),0)

    def test_distribution_destination_protected_and_closure_date_checked(self):
        from django.db.models.deletion import ProtectedError
        from core.lot_lifecycle import latest_activity
        self.feed_receipt()
        self.command('DISTRIBUTE',{'product':str(self.feed.pk),'species':self.species.pk,
            'lot':self.lot.pk,'quantity':'80'})
        out=SupplyMovement.objects.get(kind='DISTRIBUTION')
        self.assertEqual(out.destination_lot_id,self.lot.pk)
        self.assertEqual(latest_activity(self.lot),timezone.localdate())
        with self.assertRaises(ProtectedError):self.lot.delete()

    def test_reverse_correction_on_closed_lot_keeps_history(self):
        receipt=self.feed_receipt(lot=self.lot.pk)
        self.lot.date_fin=timezone.localdate();self.lot.save()
        self.command('REVERSE',{'original':receipt.pk},reason='Correction historique propriétaire')
        self.assertEqual(self.balance()['physical'],0)
        self.assertEqual(SupplyMovement.objects.count(),2)
        self.lot.refresh_from_db();self.assertIsNotNone(self.lot.date_fin)


class SupplySchemaTests(TransactionTestCase):
    def test_additive_schema_roundtrip_preserves_legacy_expense(self):
        from django.db.migrations.executor import MigrationExecutor
        owner=User.objects.create_user(username='migration-owner')
        species=Espece.objects.create(exploitation=owner.exploitation,nom='Historique')
        lot=Lot.objects.create(exploitation=owner.exploitation,espece=species,nom='Historique',date_debut=timezone.localdate())
        Depense.objects.create(lot=lot,montant='123.45',note='Historique intact')
        before=list(Depense.objects.values())
        try:
            MigrationExecutor(connection).migrate([('core','0038_passwordresetlimit')])
            self.assertNotIn('core_supplymovement',connection.introspection.table_names())
            self.assertEqual(list(Depense.objects.values()),before)
        finally:
            MigrationExecutor(connection).migrate([('core','0041_supply_foundation')])
        self.assertEqual(list(Depense.objects.values()),before)
        self.assertEqual(SupplyMovement.objects.count(),0)


class SupplyConcurrencyTests(TransactionTestCase):
    def test_parallel_commands_cannot_overdraw(self):
        if connection.vendor != 'postgresql':self.skipTest('PostgreSQL row locks required')
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from django.db import close_old_connections
        owner=User.objects.create_user(username='concurrent-owner')
        farm=owner.exploitation
        species=Espece.objects.create(exploitation=farm,nom='Test')
        lot=Lot.objects.create(exploitation=farm,espece=species,nom='Test',date_debut=timezone.localdate())
        feed=create_product(owner,farm.pk,kind='FEED',name='Concurrent')
        execute(owner,farm.pk,uuid4(),'FEED_RECEIPT',{'product':str(feed.pk),'species':species.pk,'kg':'500'})
        barrier=Barrier(2)
        def consume(_):
            close_old_connections()
            try:
                actor=User.objects.get(pk=owner.pk)
                barrier.wait(timeout=10)
                execute(actor,farm.pk,uuid4(),'DISTRIBUTE',{'product':str(feed.pk),
                    'species':species.pk,'lot':lot.pk,'quantity':'300'})
                return 'OK'
            except ValidationError:
                return 'REFUSED'
            finally:close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(consume,range(2)))
        self.assertCountEqual(results,['OK','REFUSED'])
        self.assertEqual(stock(owner,farm.pk,feed.pk)['physical'],200)
