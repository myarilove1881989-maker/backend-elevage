from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from core.models import (User, Lot, Espece, Achat, Mouvement, Vente, Depense, Client,
                         CollecteOeufs, ConsommationAliment, Payment, Lettrage, PeseeProduction)
from core.tests.functional_fixtures import operator_for_tests
from core.supply_models import SupplyProduct, SupplyOperation, SupplyReceipt, SupplyMovement
from core.cycle_report import consumption_analysis


class CycleReportTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username='cycle-owner')
        self.farm = self.owner.exploitation
        self.start = timezone.localdate() - timedelta(days=10)
        self.species = Espece.objects.create(nom='Poulets', exploitation=self.farm)
        self.lot = Lot.objects.create(nom='Cycle A', exploitation=self.farm, espece=self.species, date_debut=self.start)
        self.api = APIClient()
        self.api.force_authenticate(self.owner)
        self.api.credentials(HTTP_ACCEPT='application/json; elevage-contract=2')

    def get(self, lot=None, suffix=''):
        return self.api.get(f'/api/lots/{(lot or self.lot).pk}/cycle-report/{suffix}')

    def purchase(self, quantity=100, amount=500):
        Achat.objects.create(lot=self.lot, exploitation=self.farm, quantite=quantity,
                             prix_total=amount, prix_unitaire=Decimal(amount)/quantity, date=self.start)
        Mouvement.objects.create(lot=self.lot, exploitation=self.farm, type_mouvement='ACHAT',
                                 quantite=quantity, date=self.start)

    def test_finances_do_not_double_count_stock_or_egg_sale_and_read_does_not_write(self):
        self.purchase()
        Vente.objects.create(lot=self.lot, quantite=10, prix_unitaire=100, date=self.start)
        Depense.objects.create(lot=self.lot, montant=200, date=self.start)
        ConsommationAliment.objects.create(lot=self.lot, exploitation=self.farm, date=self.start,
                                           quantite_kg=10, prix_kg=20)
        revision = self.farm.business_revision
        r = self.get()
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data['financial']['result'], '300.00')
        self.assertEqual(r.data['financial']['charges'], '700.00')
        self.assertEqual(r.data['technical']['initial_animals'], 100)
        self.assertEqual(r.data['technical']['remaining_animals'], 100)
        self.farm.refresh_from_db()
        self.assertEqual(self.farm.business_revision, revision)
        self.assertEqual(Depense.objects.count(), 1)

    def test_missing_is_null_and_zero_charges_rate_unavailable(self):
        r = self.get().data
        self.assertIsNone(r['technical']['initial_animals'])
        self.assertIsNone(r['technical']['deaths'])
        self.assertIsNone(r['technical']['mean_weight_kg'])
        self.assertIsNone(r['technical']['feed_kg'])
        self.assertIsNone(r['financial']['result_percent'])
        self.assertEqual(r['financial']['result'], '0.00')

    def test_births_and_deaths_do_not_use_current_stock_as_initial(self):
        self.purchase()
        Mouvement.objects.create(lot=self.lot, exploitation=self.farm, type_mouvement='NAISSANCE', quantite=5, mort_nes=2, date=self.start+timedelta(days=1))
        Mouvement.objects.create(lot=self.lot, exploitation=self.farm, type_mouvement='MORTALITE', quantite=10, date=self.start+timedelta(days=2))
        t = self.get().data['technical']
        self.assertEqual(t['initial_animals'], 100)
        self.assertEqual(t['remaining_animals'], 95)
        self.assertEqual(t['births'], 5)
        self.assertEqual(t['stillbirths'], 2)
        self.assertEqual(t['mortality_percent'], '9.52')

    def test_closed_dates_and_late_payments_are_separate(self):
        self.lot.date_fin = self.start+timedelta(days=3)
        self.lot.save()
        c = Client.objects.create(exploitation=self.farm, nom='Client')
        s = Vente.objects.create(lot=self.lot, client=c, quantite=2, prix_unitaire=100, date=self.start)
        Vente.objects.create(lot=self.lot, quantite=1, prix_unitaire=999, date=self.start+timedelta(days=4))
        p = Payment.objects.create(exploitation=self.farm, client=c, montant=50, date=timezone.localdate())
        Lettrage.objects.create(vente=s, payment=p, montant=50)
        r = self.get().data
        self.assertTrue(r['closed'])
        self.assertEqual(r['financial']['revenue'], '200.00')
        self.assertEqual(r['financial']['receivables_to_date'], '150.00')
        self.assertEqual(r['technical']['duration_days'], 4)
        self.assertTrue(any('hors des dates' in w for w in r['warnings']))

    def test_voided_financial_facts_are_excluded(self):
        s = Vente.objects.create(lot=self.lot, quantite=1, prix_unitaire=100, date=self.start)
        d = Depense.objects.create(lot=self.lot, montant=30, date=self.start)
        for item in (s, d):
            item.voided_at = timezone.now()
            item.void_decision_uuid = uuid4()
            item.save()
        r = self.get().data
        self.assertEqual(r['financial']['revenue'], '0.00')
        self.assertEqual(r['financial']['expenses'], '0.00')

    def test_comparison_rules_and_points(self):
        self.purchase()
        b = Lot.objects.create(nom='Cycle B', exploitation=self.farm, espece=self.species, date_debut=self.start)
        Depense.objects.create(lot=b, montant=100, date=self.start)
        r = self.get(suffix=f'?compare={b.pk}')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data['comparison']['differences']['result']['absolute'], '-400.00')
        self.assertEqual(r.data['comparison']['differences']['result_percent']['unit'], 'points')
        self.assertIsNone(r.data['comparison']['differences']['initial_animals']['relative_percent'])
        b.type_production = 'OEUFS'; b.save()
        self.assertEqual(self.get(suffix=f'?compare={b.pk}').status_code, 400)
        self.assertEqual(self.get(suffix=f'?compare={self.lot.pk}').status_code, 400)
        self.assertEqual(self.get(suffix='?compare=invalid').status_code, 400)

    def test_tenant_and_member_and_http_methods(self):
        other = User.objects.create_user(username='foreign-cycle')
        foreign = Lot.objects.create(exploitation=other.exploitation, espece=self.species, nom='Foreign', date_debut=self.start)
        self.assertEqual(self.get(foreign).status_code, 404)
        self.assertEqual(self.get(suffix=f'?compare={foreign.pk}').status_code, 404)
        operator = operator_for_tests(self.owner, 'cycle-worker', [])
        self.api.force_authenticate(operator)
        self.assertEqual(self.get().status_code, 200)
        from core.models import ExploitationMembership
        ExploitationMembership.objects.filter(user=operator).update(is_active=False)
        self.assertEqual(self.get().status_code, 403)
        self.api.force_authenticate(self.owner)
        self.assertEqual(self.api.post(f'/api/lots/{self.lot.pk}/cycle-report/', {}).status_code, 405)
        self.api.force_authenticate(None)
        self.assertEqual(self.get().status_code, 401)

    def test_eggs_and_growth_are_specific_and_missing_days_are_not_zero(self):
        self.lot.type_production = 'OEUFS'; self.lot.save()
        CollecteOeufs.objects.create(lot=self.lot, exploitation=self.farm, nombre_collecte=20, nombre_casses=2)
        r = self.get().data
        self.assertEqual(r['technical']['eggs_collected'], 20)
        self.assertEqual(r['technical']['eggs_broken'], 2)
        self.assertNotIn('mean_weight_kg', r['technical'])
        self.lot.type_production = 'CHAIR'; self.lot.save()
        for days, kg in ((4, 10), (2, 12)):
            PeseeProduction.objects.create(lot=self.lot, exploitation=self.farm, nombre_animaux_peses=10,
                poids_total_kg=kg, pesee_at=timezone.now()-timedelta(days=days))
        t = self.get().data['technical']
        self.assertEqual(Decimal(t['mean_weight_kg']), Decimal('1.2'))
        self.assertEqual(t['daily_gain_g'], '100.00')

    def feed_entry(self, product, qty, price, days, reserved=None):
        at = timezone.now()-timedelta(days=days)
        d = Depense.objects.create(lot=self.lot, montant=price, date=at.date())
        op = SupplyOperation.objects.create(exploitation=self.farm, operation_uuid=uuid4(), author=self.owner,
            kind='FEED_PURCHASE', payload={'test': True}, fingerprint='a'*64, occurred_at=at)
        SupplyReceipt.objects.create(operation=op, product=product, expense=d, quantity=qty)
        SupplyMovement.objects.create(operation=op, line=1, product=product, species=self.species, lot=reserved, kind='ENTRY', quantity=qty)

    def feed_exit(self, product, qty, days, kind='DISTRIBUTION'):
        op = SupplyOperation.objects.create(exploitation=self.farm, operation_uuid=uuid4(), author=self.owner,
            kind='FEED_DISTRIBUTION', payload={'test': True}, fingerprint='b'*64, occurred_at=timezone.now()-timedelta(days=days))
        SupplyMovement.objects.create(operation=op, line=1, product=product, species=self.species,
            destination_lot=self.lot, kind=kind, quantity=-qty)

    def test_moving_average_shared_stock_separate_from_accounting(self):
        p = SupplyProduct.objects.create(exploitation=self.farm, kind='FEED', name='Croissance')
        self.feed_entry(p, 100, 200, 9)
        self.feed_exit(p, 50, 8)
        self.feed_entry(p, 100, 400, 7)
        self.feed_exit(p, 60, 6)
        r = self.get().data
        self.assertEqual(r['valuation']['feed_cost'], '300.00')
        self.assertEqual(r['valuation']['status'], 'documented')
        self.assertEqual(r['financial']['charges'], '600.00')
        self.assertEqual(r['financial']['result'], '-600.00')

    def test_corrected_and_unpriced_histories_are_not_invented(self):
        p = SupplyProduct.objects.create(exploitation=self.farm, kind='FEED', name='Correction')
        self.feed_entry(p, 100, 200, 9)
        self.feed_exit(p, 20, 8)
        self.feed_exit(p, 1, 7, kind='CORRECTION')
        v = consumption_analysis(self.lot, self.start, timezone.localdate())
        self.assertIsNone(v['feed_cost'])
        self.assertEqual(v['status'], 'unavailable')

    def test_negative_inventory_invalidates_valuation(self):
        p = SupplyProduct.objects.create(exploitation=self.farm, kind='FEED', name='Incomplet')
        self.feed_exit(p, 10, 8)
        self.feed_entry(p, 100, 200, 7)
        self.assertIsNone(self.get().data['valuation']['feed_cost'])

    def test_vaccine_vial_cost_and_corrected_doses(self):
        from core.models import CategorieDepense
        from core.supply_models import SupplyCategoryBinding, SupplyVial
        category = CategorieDepense.objects.create(exploitation=self.farm, nom='Vaccins')
        SupplyCategoryBinding.objects.create(category=category, purpose='VACCINE')
        product = SupplyProduct.objects.create(exploitation=self.farm, kind='VACCINE', name='Vaccin test')
        purchase = {'operation_uuid': str(uuid4()), 'lot': self.lot.pk, 'categorie': category.pk,
            'montant': '1000.00', 'date': str(timezone.localdate()),
            'vaccine_supply': {'product': str(product.pk), 'species': self.species.pk,
            'vial_count': 10, 'doses_per_vial': 20, 'manufacturer_batch': 'SYNTHETIQUE',
            'expires_on': str(timezone.localdate()+timedelta(days=30)), 'received_on': str(timezone.localdate())}}
        response = self.api.post('/api/depenses/create/', purchase, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        vials = list(SupplyVial.objects.order_by('ordinal'))
        vaccination = {'operation_uuid': str(uuid4()), 'lot': self.lot.pk, 'product': str(product.pk),
            'animals': 15, 'vaccinated_at': timezone.now().isoformat(),
            'allocations': [{'vial': str(vials[0].pk), 'doses': 15, 'lost': 5}]}
        response = self.api.post('/api/vaccinations/', vaccination, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        r = self.get().data
        self.assertEqual(r['valuation']['vaccine_cost'], '75.00')
        self.assertEqual(r['valuation']['loss_cost'], '25.00')
        self.assertEqual(r['technical']['administered_doses'], 15)
        self.assertEqual(r['technical']['lost_vaccine_doses'], 5)
        self.assertIsNone(r['valuation']['reserved_vaccine_doses_remaining'])
        created = response.data
        response = self.api.patch(f"/api/vaccinations/{created['id']}/", {
            'operation_uuid': str(uuid4()), 'expected_version': created['version'], 'reason': 'Erreur de dose',
            'vaccinated_at': created['vaccinated_at'], 'animals': 10,
            'allocations': [{'vial': str(vials[1].pk), 'doses': 10}]}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        r = self.get().data
        self.assertEqual(r['technical']['administered_doses'], 10)
        self.assertIsNone(r['valuation']['vaccine_cost'])
        self.assertEqual(r['financial']['charges'], '1000.00')

    def test_partial_cost_when_one_product_has_unknown_history(self):
        p = SupplyProduct.objects.create(exploitation=self.farm, kind='FEED', name='Documente')
        self.feed_entry(p, 100, 200, 9)
        self.feed_exit(p, 20, 8)
        q = SupplyProduct.objects.create(exploitation=self.farm, kind='FEED', name='Inconnu')
        self.feed_exit(q, 20, 8)
        r = self.get().data['valuation']
        self.assertEqual(r['status'], 'partial')
        self.assertEqual(r['feed_cost'], '40.00')
        self.assertEqual(r['unpriced_products'], 1)

    def test_legacy_feed_makes_valuation_partial_without_adding_a_charge(self):
        p = SupplyProduct.objects.create(exploitation=self.farm, kind='FEED', name='Documente')
        self.feed_entry(p, 100, 200, 9)
        self.feed_exit(p, 20, 8)
        ConsommationAliment.objects.create(lot=self.lot, exploitation=self.farm, date=self.start,
            quantite_kg=10, prix_kg=20)
        r = self.get().data
        self.assertEqual(r['valuation']['status'], 'partial')
        self.assertEqual(r['financial']['charges'], '200.00')


from core.tests.test_terrain_reconciliation import ReconciliationFixture


class CycleReconciliationTests(ReconciliationFixture, TestCase):
    def test_reversed_sale_compensation_preserves_original_and_cycle_stock(self):
        operation = self.sale()
        self.apply(operation)
        self.assertEqual(self.submit_decision(operation, self.decision(operation, 'REVERSE')).status_code, 200)
        api = APIClient()
        api.force_authenticate(self.owner)
        response = api.get(f'/api/lots/{self.lot.pk}/cycle-report/')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['financial']['revenue'], '0.00')
        self.assertEqual(response.data['technical']['remaining_animals'], 20)
        self.assertIsNone(response.data['technical']['mortality_percent'])
        self.assertEqual(Vente.history.count(), 1)
        self.assertEqual(Mouvement.objects.filter(type_mouvement='VENTE').count(), 1)
