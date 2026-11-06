"""API contract evidence; no claim of building or executing a physical APK."""
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from django.conf import settings
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from core.models import User, Espece, Lot, Client, CategorieDepense, Depense, Vente, Payment
from core.supply_models import SupplyProduct, SupplyCategoryBinding, SupplyVial
from core.supply_services import stock
from core.tests.functional_fixtures import operator_for_tests


class Android113WebCompatibilityTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username='compat113-owner', password='SyntheticOnly!113')
        self.farm = self.owner.exploitation
        self.operator = operator_for_tests(self.owner, 'compat113-operator',
            ['agenda.read', 'agenda.progress', 'vaccinations.create'])
        self.species = Espece.objects.create(nom='Porc', exploitation=self.farm)
        self.customer = Client.objects.create(nom='Client fictif', exploitation=self.farm)
        self.api = APIClient()
        self.api.force_authenticate(self.owner)
        self.api.credentials(HTTP_ACCEPT='application/json; elevage-contract=2')
        self.today = timezone.localdate().isoformat()

    def post(self, path, body, status=201):
        response = self.api.post('/api/' + path, body, format='json')
        self.assertEqual(response.status_code, status, response.data)
        return response.data

    def purchase(self):
        return self.post('achats/create/', {'nom_lot':'Porcs adultes fictifs', 'espece':self.species.pk,
            'quantite':20, 'prix_total':'200000.00', 'prix_unitaire':'10000.00',
            'date':self.today, 'type_production':'REPRODUCTION'})['lot_id']

    def test_all_android113_source_endpoint_templates_still_resolve(self):
        import json,re
        from pathlib import Path
        from django.urls import resolve
        reference=Path(__file__).resolve().parents[2]/'docs/android113-source-contract-inventory.json'
        inventory=json.loads(reference.read_text(encoding='utf-8'))
        for template in inventory['endpoint_templates']:
            path=template.replace('$baseUrl','').split('?')[0]
            path=re.sub(r'\$\{[^}]+\}', '00000000-0000-4000-8000-000000000001', path)
            path=re.sub(r'\$[A-Za-z_][A-Za-z0-9_]*',lambda m:'00000000-0000-4000-8000-000000000001' if 'operation' in m[0].lower() else '1',path)
            with self.subTest(template=template):
                self.assertIsNotNone(resolve('/api'+path).func)

    def test_legacy_jwt_fields_refresh_and_finite_lifetimes(self):
        api = APIClient()
        response = api.post('/api/token/', {'username':self.owner.username,'password':'SyntheticOnly!113'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIsInstance(response.data['access'], str)
        self.assertIsInstance(response.data['refresh'], str)
        renewed = api.post('/api/token/refresh/', {'refresh':response.data['refresh']}, format='json')
        self.assertEqual(renewed.status_code, 200, renewed.data)
        self.assertIsInstance(renewed.data['access'], str)
        api.credentials(HTTP_AUTHORIZATION='Bearer ' + renewed.data['access'])
        self.assertEqual(api.get('/api/me/capabilities/').status_code, 200)
        self.assertGreater(settings.SIMPLE_JWT['ACCESS_TOKEN_LIFETIME'], timedelta(0))
        self.assertLessEqual(settings.SIMPLE_JWT['ACCESS_TOKEN_LIFETIME'], timedelta(hours=1))
        self.assertLessEqual(settings.SIMPLE_JWT['REFRESH_TOKEN_LIFETIME'], timedelta(days=7))

    def test_old_capability_contract_and_lot_types_are_preserved(self):
        lot_id = self.purchase()
        old = APIClient(); old.force_authenticate(self.owner)
        contract = old.get('/api/me/capabilities/').data['permission_contract']
        self.assertEqual(contract['version'], 1)
        self.assertNotIn('vaccinations.create', contract['permissions'])
        response = old.get(f'/api/lots/{lot_id}/')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIsInstance(response.data['id'], int)
        self.assertIsInstance(response.data['stock'], int)
        self.assertEqual(response.data['stock'], 20)
        self.assertEqual(response.data['prix_unitaire_origine'], '10000.00')
        foreign = User.objects.create_user(username='compat113-other')
        old.force_authenticate(foreign)
        denied = old.get(f'/api/lots/{lot_id}/')
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(denied.data, {'error':'Accès interdit'})

    def test_existing_single_date_assignee_stays_integer_and_does_not_consume_stock(self):
        lot_id = self.purchase()
        product = SupplyProduct.objects.create(exploitation=self.farm, kind='VACCINE', name='Vaccin fictif')
        body = {'operation_uuid':str(uuid4()), 'product':str(product.pk), 'species':self.species.pk,
            'lot':lot_id,'date':self.today,'assigned_to':self.operator.pk,'planned_time':None}
        result = self.post('tasks/health-plan/', body)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['assigned_to'], self.operator.pk)
        self.assertIsInstance(result[0]['assigned_to'], int)
        self.assertEqual(result[0]['date'], self.today)
        self.assertIsNone(result[0]['health']['responsible'])
        self.assertEqual(Depense.objects.count(), 0)
        self.assertEqual(SupplyVial.objects.count(), 0)
        self.post('tasks/health-plan/', body)

    def test_complete_porcine_journey_finance_stock_births_and_partial_payments(self):
        lot_id = self.purchase()
        feed_category = CategorieDepense.objects.create(exploitation=self.farm, nom='Aliment fictif')
        vaccine_category = CategorieDepense.objects.create(exploitation=self.farm, nom='Vaccin fictif')
        SupplyCategoryBinding.objects.create(category=feed_category, purpose='FEED')
        SupplyCategoryBinding.objects.create(category=vaccine_category, purpose='VACCINE')
        feed = SupplyProduct.objects.create(exploitation=self.farm, kind='FEED', name='Aliment porc fictif')
        vaccine = SupplyProduct.objects.create(exploitation=self.farm, kind='VACCINE', name='Vaccin porc fictif')
        feed_purchase = {'operation_uuid':str(uuid4()),'lot':lot_id,'categorie':feed_category.pk,
            'montant':'12500.00','date':self.today,'feed_supply':{'product':str(feed.pk),
                'species':self.species.pk,'kg':'500','received_on':self.today}}
        first = self.post('depenses/create/', feed_purchase)
        self.assertEqual(self.post('depenses/create/', feed_purchase)['id'], first['id'])
        self.post('alimentation/distributions/', {'operation_uuid':str(uuid4()),'stock_version':1,
            'lot':lot_id,'product':str(feed.pk),'source_lot':None,'quantite_kg':'50',
            'distribution_at':timezone.now().isoformat()})
        self.assertEqual(stock(self.owner,self.farm.pk,feed.pk)['physical'], Decimal('450'))
        self.post('depenses/create/', {'operation_uuid':str(uuid4()),'lot':lot_id,
            'categorie':vaccine_category.pk,'montant':'1000.00','date':self.today,
            'vaccine_supply':{'product':str(vaccine.pk),'species':self.species.pk,'vial_count':1,
                'doses_per_vial':20,'manufacturer_batch':'FICTIF','received_on':self.today,
                'expires_on':(timezone.localdate()+timedelta(days=30)).isoformat()}})
        vial = SupplyVial.objects.get()
        vaccination = {'operation_uuid':str(uuid4()),'lot':lot_id,'product':str(vaccine.pk),
            'animals':10,'vaccinated_at':timezone.now().isoformat(),'allocations':[{'vial':str(vial.pk),'doses':10}]}
        self.post('vaccinations/', vaccination); self.post('vaccinations/', vaccination)
        self.assertEqual(sum(v['physical'] for v in self.api.get('/api/vaccine-supplies/').data['vials']), 10)
        transport_category = CategorieDepense.objects.create(exploitation=self.farm, nom='Transport fictif')
        self.post('depenses/create/', {'lot':lot_id,'categorie':transport_category.pk,'montant':'2500.00','date':self.today,'note':'Transport fictif'})
        for quantity, price in [(3,'25000.00'),(2,'26000.00')]:
            self.post('mouvements/create/', {'lot':lot_id,'type_mouvement':'VENTE','quantite':quantity,
                'prix_unitaire':price,'client':self.customer.pk,'date':self.today})
        before_expenses = Depense.objects.count()
        self.post('mouvements/create/', {'lot':lot_id,'type_mouvement':'MORTALITE',
            'quantite':1,'prix_unitaire':'10000.00','date':self.today})
        born = self.post('mouvements/create/', {'lot':lot_id,'type_mouvement':'NAISSANCE',
            'total_naissances':12,'mort_nes':2,'nom_nouveau_lot':'Porcelets fictifs','date':self.today})
        self.assertEqual(Lot.objects.get(pk=lot_id).stock, 14)
        child = self.api.get(f"/api/lots/{born['nouveau_lot']['id']}/").data
        self.assertEqual(child['stock'], 10); self.assertEqual(child['prix_unitaire_origine'], '0.00')
        self.assertEqual(Depense.objects.count(), before_expenses)
        self.assertEqual(Vente.objects.count(), 2)
        sale = Vente.objects.order_by('pk').first()
        for amount in ['10000.00','20000.00']:
            self.post('payments/create/', {'client':self.customer.pk,'vente':sale.pk,'montant':amount})
        invoices = self.api.get(f'/api/clients/{self.customer.pk}/ventes/').data
        invoice = next(row for row in invoices if row['id']==sale.pk)
        self.assertEqual(Decimal(str(invoice['montant_paye'])), Decimal('30000'))
        self.assertEqual(Decimal(str(invoice['reste'])), Decimal('45000'))
        self.assertEqual(Payment.objects.count(), 2)
        report = self.api.get(f'/api/lots/{lot_id}/cycle-report/').data
        self.assertEqual(report['financial']['result'], '-89000.00')
        self.assertEqual(report['financial']['charges'], '216000.00')
        self.assertEqual(Depense.objects.count(), 3)
