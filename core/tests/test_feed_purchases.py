from datetime import timedelta
from uuid import uuid4
from decimal import Decimal
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from core.models import User, Espece, Lot, CategorieDepense, Depense, ConsommationAliment, Achat, Mouvement
from core.supply_models import SupplyCategoryBinding, SupplyProduct, SupplyOperation, SupplyMovement, SupplyReceipt
from core.supply_services import create_product, execute, stock


class FeedPurchaseTests(TestCase):
    def setUp(self):
        self.owner=User.objects.create_user(username='purchase-owner')
        self.farm=self.owner.exploitation
        self.species=Espece.objects.create(exploitation=self.farm,nom='Poulets')
        self.lot=Lot.objects.create(exploitation=self.farm,espece=self.species,nom='Lot A',date_debut=timezone.localdate()-timedelta(days=10))
        self.category=CategorieDepense.objects.create(exploitation=self.farm,nom='Libellé renommé')
        SupplyCategoryBinding.objects.create(category=self.category,purpose='FEED')
        self.product=create_product(self.owner,self.farm.pk,kind='FEED',name='Croissance')
        self.api=APIClient();self.api.force_authenticate(self.owner)
        self.api.credentials(HTTP_ACCEPT='application/json; elevage-contract=2')

    def body(self,**supply):
        return {'operation_uuid':str(uuid4()),'lot':self.lot.pk,'categorie':self.category.pk,
            'montant':'12500.00','date':str(timezone.localdate()),'note':'Achat test',
            'feed_supply':{'product':str(self.product.pk),'species':self.species.pk,'kg':'500',
                'received_on':str(timezone.localdate()),**supply}}

    def buy(self,body=None):
        response=self.api.post('/api/depenses/create/',body or self.body(),format='json')
        self.assertEqual(response.status_code,201,response.data)
        return response.data

    def total(self):return stock(self.owner,self.farm.pk,self.product.pk)['physical']

    def test_500_kg_and_two_purchases(self):
        first=self.buy();self.buy()
        self.assertEqual(self.total(),1000)
        self.assertEqual(Depense.objects.count(),2);self.assertEqual(SupplyReceipt.objects.count(),2)
        self.assertEqual(SupplyMovement.objects.count(),2)
        self.assertEqual(SupplyReceipt.objects.get(expense_id=first['id']).quantity,500)
        self.assertEqual(Achat.objects.count(),0);self.assertEqual(Mouvement.objects.count(),0)

    def test_ten_sacks_normalize_to_500(self):
        body=self.body(bags='10',kg_per_bag='50');del body['feed_supply']['kg']
        self.buy(body);self.assertEqual(self.total(),500)

    def test_lost_response_retry_and_double_click(self):
        body=self.body();first=self.buy(body);second=self.buy(body)
        self.assertEqual(first['id'],second['id']);self.assertTrue(second['replayed'])
        self.assertEqual(Depense.objects.count(),1);self.assertEqual(self.total(),500)
        body['montant']='13000'
        self.assertEqual(self.api.post('/api/depenses/create/',body,format='json').status_code,400)
        self.assertEqual(Depense.objects.count(),1)

    def test_new_product_created_atomically(self):
        body=self.body();del body['feed_supply']['product']
        body['feed_supply']['new_product']={'id':str(uuid4()),'name':'Nouvel aliment'}
        first=self.buy(body);self.buy(body)
        self.assertEqual(SupplyProduct.objects.count(),2);self.assertEqual(Depense.objects.count(),1)
        self.assertEqual(SupplyReceipt.objects.get(expense_id=first['id']).product.name,'Nouvel aliment')

    def test_invalid_supply_rolls_back_finance_and_new_product(self):
        body=self.body(kg='-1');del body['feed_supply']['product']
        body['feed_supply']['new_product']={'id':str(uuid4()),'name':'Ne doit pas rester'}
        self.assertEqual(self.api.post('/api/depenses/create/',body,format='json').status_code,400)
        self.assertEqual(Depense.objects.count(),0);self.assertEqual(SupplyProduct.objects.count(),1)

    def test_quantity_correction_history_and_financial_unchanged(self):
        first=self.buy();before=list(Depense.objects.values())
        body={'operation_uuid':str(uuid4()),'quantity_kg':'600','reason':'Erreur de quantité'}
        url=f"/api/depenses/supply/{first['id']}/"
        self.assertEqual(self.api.patch(url,body,format='json').status_code,200)
        self.assertEqual(self.api.patch(url,body,format='json').status_code,200)
        self.assertEqual(self.total(),600)
        self.assertEqual(list(Depense.objects.values()),before)
        self.assertEqual(SupplyReceipt.objects.first().quantity,500)
        self.assertEqual(SupplyMovement.objects.count(),2)

    def test_cancellation_compensates_corrected_quantity(self):
        first=self.buy();expense_id=first['id']
        self.api.patch(f'/api/depenses/supply/{expense_id}/',{'operation_uuid':str(uuid4()),'quantity_kg':'600','reason':'Rectification'},format='json')
        body={'operation_uuid':str(uuid4()),'reason':'Annulation de l’achat'}
        url=f'/api/depenses/cancel/{expense_id}/'
        response=self.api.post(url,body,format='json');self.assertEqual(response.status_code,200,response.data)
        self.assertEqual(self.api.post(url,body,format='json').status_code,200)
        self.assertEqual(self.total(),0);self.assertEqual(Depense.objects.count(),0)
        self.assertEqual(Depense.history.count(),1);self.assertEqual(SupplyMovement.objects.count(),3)

    def test_consumed_stock_cannot_be_cancelled_or_overcorrected(self):
        first=self.buy()
        execute(self.owner,self.farm.pk,uuid4(),'DISTRIBUTE',{'product':str(self.product.pk),'species':self.species.pk,'lot':self.lot.pk,'quantity':'80'})
        response=self.api.post(f"/api/depenses/cancel/{first['id']}/",{'operation_uuid':str(uuid4()),'reason':'Annulation'},format='json')
        self.assertEqual(response.status_code,400,response.data)
        self.assertEqual(self.total(),420)
        response=self.api.patch(f"/api/depenses/supply/{first['id']}/",{'operation_uuid':str(uuid4()),'quantity_kg':'50','reason':'Correction'},format='json')
        self.assertEqual(response.status_code,400)
        self.assertEqual(Depense.objects.count(),1)

    def test_linked_legacy_consumption_requires_review(self):
        first=self.buy();ConsommationAliment.objects.create(exploitation=self.farm,lot=self.lot,
            quantite_kg=5,aliment='Ancien lien',depense_id=first['id'])
        response=self.api.post(f"/api/depenses/cancel/{first['id']}/",{'operation_uuid':str(uuid4()),'reason':'Annulation'},format='json')
        self.assertEqual(response.status_code,400);self.assertEqual(self.total(),500)

    def test_closed_lot_and_foreign_product_refused(self):
        other=User.objects.create_user(username='purchase-foreign')
        product=create_product(other,other.exploitation_id,kind='FEED',name='Étranger')
        self.assertEqual(self.api.post('/api/depenses/create/',self.body(product=str(product.pk)),format='json').status_code,400)
        self.assertEqual(Depense.objects.count(),0)
        self.lot.date_fin=timezone.localdate();self.lot.save()
        self.assertEqual(self.api.post('/api/depenses/create/',self.body(),format='json').status_code,400)

    def test_stock_sharing_and_reserved_lot(self):
        self.buy();self.buy(self.body(lot=self.lot.pk))
        self.assertEqual(self.total(),1000)
        positions=self.api.get('/api/feed-supplies/').data['products'][0]['positions']
        self.assertEqual({row['lot_id'] for row in positions},{None,self.lot.pk})

    def test_category_code_survives_rename_and_other_categories_unchanged(self):
        categories=self.api.get('/api/categories-depense/').data
        self.assertEqual(categories[0]['stock_purpose'],'FEED')
        ordinary=CategorieDepense.objects.create(exploitation=self.farm,nom='Transport')
        body=self.body();body['categorie']=ordinary.pk;del body['feed_supply'];del body['operation_uuid']
        self.buy(body);self.assertEqual(self.total(),0)
        self.assertEqual(SupplyReceipt.objects.count(),0)

    def test_financial_kpis_follow_existing_expense_rules(self):
        before=self.api.get('/api/dashboard/').data['kpis']
        self.buy()
        after=self.api.get('/api/dashboard/').data['kpis']
        self.assertEqual(after['chiffre_affaires'],before['chiffre_affaires'])
        self.assertEqual(Decimal(str(after['depenses']))-Decimal(str(before['depenses'])),Decimal('12500'))
        self.assertEqual(Decimal(str(after['marge']))-Decimal(str(before['marge'])),Decimal('-12500'))

    def test_authorized_classic_operator_can_buy_but_not_correct(self):
        from core.tests.functional_fixtures import operator_for_tests
        operator=operator_for_tests(self.owner,'feed-purchase-operator',['expenses.create'])
        self.api.force_authenticate(operator)
        first=self.buy()
        self.assertEqual(SupplyReceipt.objects.get(expense_id=first['id']).operation.author_id,operator.pk)
        response=self.api.patch(f"/api/depenses/supply/{first['id']}/",{'operation_uuid':str(uuid4()),'quantity_kg':'600','reason':'Test'},format='json')
        self.assertEqual(response.status_code,403)

    def test_phase1_reversal_cannot_bypass_coordinated_cancellation(self):
        self.buy();op=SupplyOperation.objects.get(kind='FEED_PURCHASE')
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):execute(self.owner,self.farm.pk,uuid4(),'REVERSE',{'original':op.pk},reason='Interdit')

    def test_foreign_catalog_and_correction_refused(self):
        first=self.buy();other=User.objects.create_user(username='foreign-reader')
        self.api.force_authenticate(other)
        self.assertEqual(self.api.get('/api/feed-supplies/').data['products'],[])
        response=self.api.patch(f"/api/depenses/supply/{first['id']}/",{'operation_uuid':str(uuid4()),'quantity_kg':'600','reason':'Test'},format='json')
        self.assertEqual(response.status_code,400)

    def test_owner_actions_offer_and_apply_quantity_correction_and_cancel(self):
        first=self.buy();url=f"/api/maintenance/expenses/{first['id']}/"
        metadata=self.api.get(url).data
        self.assertEqual([field['name'] for field in metadata['fields']],['quantity_kg'])
        body={'farm':self.farm.pk,'expected_revision':metadata['revision'],'confirm':True,
            'reason':'Quantité vérifiée','values':{'quantity_kg':'600'}}
        response=self.api.patch(url,body,format='json');self.assertEqual(response.status_code,200,response.data)
        self.assertEqual(self.total(),600)
        body['expected_revision']=response.data['revision'];body['values']={}
        response=self.api.delete(url,body,format='json');self.assertEqual(response.status_code,200,response.data)
        self.assertEqual(self.total(),0);self.assertEqual(Depense.history.count(),1)

    def test_category_seed_only_binds_unambiguous_historical_categories(self):
        from importlib import import_module
        from django.apps import apps
        seed=import_module('core.migrations.0043_feed_category_bindings').seed
        first=CategorieDepense.objects.create(exploitation=self.farm,nom='Aliment')
        seed(apps,None)
        self.assertEqual(SupplyCategoryBinding.objects.get(category=first).purpose,'FEED')
        first.nom='Nutrition';first.save();seed(apps,None)
        self.assertTrue(SupplyCategoryBinding.objects.filter(category=first).exists())
        other=User.objects.create_user(username='ambiguous-categories')
        cats=[CategorieDepense.objects.create(exploitation=other.exploitation,nom='Aliment') for _ in range(2)]
        seed(apps,None)
        self.assertFalse(SupplyCategoryBinding.objects.filter(category__in=cats).exists())

    def test_closed_purchase_correction_and_cancellation_refused(self):
        first=self.buy();self.lot.date_fin=timezone.localdate();self.lot.save()
        correction={'operation_uuid':str(uuid4()),'quantity_kg':'600','reason':'Correction'}
        self.assertEqual(self.api.patch(f"/api/depenses/supply/{first['id']}/",correction,format='json').status_code,400)
        self.assertEqual(self.api.post(f"/api/depenses/cancel/{first['id']}/",{'operation_uuid':str(uuid4()),'reason':'Annulation'},format='json').status_code,400)
        self.assertEqual(self.total(),500);self.assertEqual(Depense.objects.count(),1)

    def test_malformed_accounting_references_refused_without_partial_effects(self):
        for field in ('lot','categorie'):
            body=self.body();body[field]='not-an-id'
            response=self.api.post('/api/depenses/create/',body,format='json')
            self.assertEqual(response.status_code,400,response.data)
        self.assertEqual(Depense.objects.count(),0);self.assertEqual(SupplyReceipt.objects.count(),0)


from django.test import TransactionTestCase
from django.db import connection, close_old_connections


class FeedPurchaseConcurrencyTests(TransactionTestCase):
    def test_same_request_in_parallel_creates_one_expense_and_entry(self):
        if connection.vendor!='postgresql':self.skipTest('PostgreSQL concurrency check')
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        fixture=FeedPurchaseTests('test_500_kg_and_two_purchases');fixture.setUp()
        body=fixture.body();barrier=Barrier(2)
        def send(_):
            close_old_connections()
            try:
                api=APIClient();api.force_authenticate(User.objects.get(pk=fixture.owner.pk))
                api.credentials(HTTP_ACCEPT='application/json; elevage-contract=2')
                barrier.wait(timeout=10)
                response=api.post('/api/depenses/create/',body,format='json')
                return response.status_code,response.data.get('id')
            finally:close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(send,range(2)))
        self.assertEqual([row[0] for row in results],[201,201])
        self.assertEqual(results[0][1],results[1][1]);self.assertEqual(Depense.objects.count(),1)
        self.assertEqual(SupplyMovement.objects.count(),1)
