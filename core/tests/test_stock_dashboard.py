from uuid import uuid4
from django.test import TestCase
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient
from core.tests.test_terrain_operations import OperationFixture
from core.tests import test_vaccines as fixtures
from core.models import MembershipPermission, Depense, CategorieDepense
from core.supply_models import SupplyProduct, SupplyMovement, SupplyCategoryBinding
from core.vaccine_views import snapshot
from core.offline_permissions import contract_for
from core.access_policy import effective_permissions

class StockDashboardTests(OperationFixture,TestCase):
    def setUp(self):
        super().setUp()
        self.category=CategorieDepense.objects.create(exploitation=self.farm,nom='Vaccin renommable')
        SupplyCategoryBinding.objects.create(category=self.category,purpose='VACCINE')
        self.product=SupplyProduct.objects.create(exploitation=self.farm,kind='VACCINE',name='Vaccin synthÃƒÂ©tique')
        for code in ['vaccinations.create','vaccinations.update','vaccinations.delete','vaccinations.loss']:
            MembershipPermission.objects.get_or_create(membership=self.member,code=code)
        self.grant.capabilities.update(vaccine_stock_contract=1,permission_contract=contract_for(self.grant,effective_permissions(self.member)))
        self.grant.save()
        self.api=APIClient();self.api.force_authenticate(self.owner);self.api.credentials(HTTP_ACCEPT='application/json; elevage-contract=2')
    body=fixtures.VaccineTests.body
    buy=fixtures.VaccineTests.buy
    vials=fixtures.VaccineTests.vials
    vaccination=fixtures.VaccineTests.vaccination
    def test_threshold_configuration_retry_and_no_financial_effect(self):
        self.buy();before=list(Depense.objects.values_list('montant',flat=True))
        url=f'/api/supply-products/{self.product.pk}/threshold/'
        body={'alert_threshold':'250','expected_threshold':'0'}
        for _ in range(2):self.assertEqual(self.api.patch(url,body,format='json').status_code,200)
        self.assertEqual(self.api.get('/api/vaccine-supplies/').data['products'][0]['alert_threshold'],'250.000')
        self.assertEqual(list(Depense.objects.values_list('montant',flat=True)),before)
        self.assertEqual(SupplyMovement.objects.count(),10)
        self.assertEqual(self.api.patch(url,{'alert_threshold':'260','expected_threshold':'0'},format='json').status_code,400)
        self.assertEqual(self.api.patch(url,{'alert_threshold':'1.5','expected_threshold':'250'},format='json').status_code,400)
    def test_history_pagination_administration_and_retry(self):
        self.buy();body=self.vaccination()
        for _ in range(2):self.assertEqual(self.api.post('/api/vaccinations/',body,format='json').status_code,201)
        url=f'/api/supply-products/{self.product.pk}/history/'
        first=self.api.get(url+'?limit=5').data
        self.assertEqual(len(first['results']),5);self.assertIsNotNone(first['next_before'])
        rest=self.api.get(url+f"?limit=100&before={first['next_before']}").data
        ids=[x['id'] for x in first['results']+rest['results']]
        self.assertEqual(len(ids),len(set(ids)));self.assertEqual(len(ids),12)
        self.assertEqual(sum(float(x['quantity']) for x in first['results']+rest['results']),165)
    def test_foreign_product_and_unscoped_uuid_cannot_disclose_history(self):
        self.buy();foreign=str(uuid4())
        self.assertEqual(self.api.get(f'/api/supply-products/{foreign}/history/').status_code,404)
        self.assertEqual(self.api.patch(f'/api/supply-products/{foreign}/threshold/',{'alert_threshold':'1','expected_threshold':'0'},format='json').status_code,404)
        self.assertEqual(self.api.get(f'/api/supply-products/{self.product.pk}/history/?limit=101').status_code,400)
    def test_operator_cannot_configure_and_revoked_stock_read_refuses_cache(self):
        self.api.force_authenticate(self.jean)
        url=f'/api/supply-products/{self.product.pk}/threshold/'
        self.assertEqual(self.api.patch(url,{'alert_threshold':'1','expected_threshold':'0'},format='json').status_code,403)
        self.member.is_active=False;self.member.save(update_fields=['is_active'])
        for url in ['/api/vaccine-supplies/','/api/feed-supplies/',f'/api/supply-products/{self.product.pk}/history/']:
            self.assertEqual(self.api.get(url).status_code,403,url)
        response=self.api.get('/api/cache-page/',{'collection':'vaccine_stock'})
        self.assertEqual(response.status_code,403)
    def test_snapshot_queries_constant_with_more_vials_and_balance_matches_ledger(self):
        self.buy()
        with CaptureQueriesContext(connection) as one:first=snapshot(self.farm.pk)
        self.buy()
        with CaptureQueriesContext(connection) as two:second=snapshot(self.farm.pk)
        self.assertEqual(len(one),len(two));self.assertLessEqual(len(two),15)
        self.assertEqual(sum(v['physical'] for v in first['vials']),200)
        self.assertEqual(sum(v['usable'] for v in second['vials']),400)
        self.assertIn('as_of',second)
    def test_old_feed_catalog_contract_kept_with_threshold_and_timestamp(self):
        SupplyProduct.objects.create(exploitation=self.farm,kind='FEED',name='Aliment',alert_threshold='80.125')
        data=self.api.get('/api/feed-supplies/').data
        self.assertTrue(data['confirmed']);self.assertEqual(data['stock_contract'],1)
        self.assertEqual(data['products'][0]['alert_threshold'],'80.125');self.assertIn('as_of',data)
        self.assertEqual(data['products'][0]['positions'],[])

    def test_old_cache_snapshot_is_unchanged_unless_presentation_requested(self):
        from core.feed_stock import snapshot as feed_snapshot
        product=SupplyProduct.objects.create(exploitation=self.farm,kind='FEED',name='Stable',alert_threshold='20')
        old=feed_snapshot(self.farm.pk)
        self.assertNotIn('as_of',old);self.assertNotIn('alert_threshold',old['products'][0])
        enriched=feed_snapshot(self.farm.pk,presentation=True)
        self.assertEqual(enriched['products'][0]['alert_threshold'],'20.000')
        self.assertEqual(old['products'][0]['positions'],enriched['products'][0]['positions'])
        self.assertEqual(set(old),{'id','exploitation','schema','confirmed_business_revision','products','coverage'})
        response=self.api.get('/api/cache-page/',{'collection':'feed_stock','presentation':'1'})
        self.assertEqual(response.status_code,200);self.assertIn('as_of',response.data['results'][0])
    def test_snapshot_capacity_refuses_instead_of_silently_truncating(self):
        from core.feed_stock import snapshot as feed_snapshot
        from rest_framework.exceptions import ValidationError
        SupplyProduct.objects.bulk_create([SupplyProduct(exploitation=self.farm,kind='FEED',name=f'Feed {i}') for i in range(1001)])
        with self.assertRaises(ValidationError):feed_snapshot(self.farm.pk)
        # Saturated feed catalog must not break the independent vaccine catalog.
        self.assertEqual(snapshot(self.farm.pk)['products'][0]['id'],str(self.product.pk))
    def test_real_foreign_product_is_not_readable_or_configurable(self):
        from core.models import User
        user=User.objects.create_user(username='other-dashboard-owner')
        foreign=SupplyProduct.objects.create(exploitation=user.exploitation,kind='FEED',name='Private')
        self.assertEqual(self.api.get(f'/api/supply-products/{foreign.pk}/history/').status_code,404)
        self.assertEqual(self.api.patch(f'/api/supply-products/{foreign.pk}/threshold/',{'alert_threshold':'10','expected_threshold':'0'},format='json').status_code,404)
        foreign.refresh_from_db();self.assertEqual(foreign.alert_threshold,0)
