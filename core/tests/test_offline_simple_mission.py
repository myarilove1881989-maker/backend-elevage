import copy
from datetime import timedelta, datetime
from django.test import TestCase, TransactionTestCase, skipUnlessDBFeature
from django.utils import timezone
from rest_framework.test import APIClient
from core.tests.test_terrain_operations import OperationFixture
from core.models import Client, Vente, TerrainSubmission, TerrainStockAdjustment, MembershipPermission, Depense, Payment, OfflineAuthorization
from core.offline_permissions import contract_for
from core.access_policy import effective_permissions
from core.foundation_views import revoke_grants


class SimpleOfflineMissionTests(OperationFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.customer = Client.objects.create(exploitation=self.farm,nom='Client synthétique')
        self.grant.capabilities['permission_contract']=contract_for(self.grant,effective_permissions(self.member))
        self.grant.save()
        self.owner_api=APIClient();self.owner_api.force_authenticate(self.owner)

    def sale(self):
        return self.terrain('VENTE_ANIMAUX',{'lot_ref':self.lot_ref(),
            'client_ref':{'server_id':self.customer.pk},'quantite':3,'prix_unitaire':'10.00'})

    def cancel(self, original, sequence=2):
        data=copy.deepcopy(self.terrain(original['entity_type'],{
            'original_operation_id':original['client_operation_id'],'reason':'Erreur de saisie',
            'lot_ref':original['payload']['lot_ref']},sequence,[original['client_operation_id']]))
        data.update(operation_type='REVERSE',local_entity_id=None)
        return data

    def ordinary_change(self, codes=None):
        self.member.refresh_from_db()
        response=self.owner_api.patch(f'/api/memberships/{self.member.pk}/',
            {'expected_version':self.member.version,'permissions':codes or ['animal_sales.cancel']},format='json')
        self.assertEqual(response.status_code,200,response.data)
        self.grant.refresh_from_db()

    def history(self, **filters):
        response=self.owner_api.get('/api/history/',{'view':'operations',**filters})
        self.assertEqual(response.status_code,200,response.data)
        return response.data['results']

    def test_ordinary_retraction_old_sale_applies_once_and_is_visible(self):
        sale=self.sale();sale['local_recorded_at']=(timezone.now()-timedelta(seconds=30)).isoformat()
        self.ordinary_change()
        self.assertEqual(self.apply(sale)['business_status'],'CONFIRMED')
        self.assertEqual(self.apply(sale)['business_status'],'CONFIRMED')
        self.assertEqual(Vente.objects.count(),1);self.assertEqual(self.lot.stock,17)
        self.assertTrue(self.history()[0]['ordinary_business_tolerance'])
        self.assertEqual(self.history()[0]['authorization_id'],str(self.grant.pk))

    def test_unknown_security_and_unclassified_version_gap_stay_blocking(self):
        self.grant.revoked_at=timezone.now();self.grant.save()
        self.ordinary_change()
        self.assertEqual(self.apply(self.sale())['business_status'],'NEEDS_RECONCILIATION')
        self.assertFalse(Vente.objects.exists())

    def test_refused_stock_original_still_identifies_receipt_under_old_permissions(self):
        original=self.sale();original['payload']['quantite']=21;self.ordinary_change()
        self.assertEqual(self.apply(original)['reason_code'],'STOCK_INSUFFICIENT')
        row=self.history()[0]
        self.assertTrue(row['received_with_old_permissions'])
        self.assertFalse(row['ordinary_business_tolerance']);self.assertIsNone(row['applied_at'])

    def test_security_overrides_previous_business_tolerance(self):
        self.ordinary_change();revoke_grants(self.member.offline_authorizations.all())
        self.grant.refresh_from_db();self.assertEqual(self.grant.revocation_kind,'SECURITY')
        self.assertEqual(self.apply(self.sale())['business_status'],'NEEDS_RECONCILIATION')

    def test_two_ordinary_changes_and_gap(self):
        self.ordinary_change();self.ordinary_change(['expenses.create'])
        self.assertEqual(self.apply(self.sale())['business_status'],'CONFIRMED')

    def test_unclassified_version_change_cannot_be_bridged(self):
        self.member.version+=1;self.member.save();self.ordinary_change()
        self.assertEqual(self.apply(self.sale())['business_status'],'NEEDS_RECONCILIATION')

    def test_device_status_reports_changed_personal_permissions_without_http_identity(self):
        original=self.sale();self.ordinary_change()
        response,_=self.send(original,purpose='STATUS')
        self.assertEqual(response.status_code,200,response.data)
        state=next(row for row in response.data['profiles'] if row['user_id']==self.jean.pk)
        self.assertEqual(state['rights_version'],2);self.assertTrue(state['active'])
        self.assertNotIn('animal_sales.create',state['permissions'])
        self.assertIn('animal_sales.create',self.grant.capabilities['permission_contract']['permissions'])

    def test_sale_cancellation_keeps_original_stock_finance_and_retry(self):
        original=self.sale();self.apply(original);cancel=self.cancel(original)
        self.assertEqual(self.apply(cancel)['business_status'],'CONFIRMED')
        self.assertEqual(self.apply(cancel)['business_status'],'CONFIRMED')
        self.assertEqual(self.lot.stock,20);self.assertFalse(Vente.objects.exists())
        self.assertEqual(Vente.history.count(),1);self.assertEqual(TerrainStockAdjustment.objects.count(),1)
        self.assertEqual(TerrainStockAdjustment.objects.get().occurred_at,
                         datetime.fromisoformat(cancel['business_occurred_at']))
        self.assertEqual(TerrainSubmission.objects.count(),2)
        self.assertEqual(TerrainSubmission.objects.get(client_operation_id=original['client_operation_id']).payload,original['payload'])
        rows=self.history();self.assertEqual(len(rows),2)
        original_row=next(r for r in rows if r['operation_type']=='CREATE')
        self.assertEqual(original_row['business_status'],'SUPERSEDED')
        self.assertEqual(original_row['cancellations'][0]['operation_id'],cancel['client_operation_id'])
        self.assertEqual(self.apply(self.cancel(original,3))['business_status'],'NEEDS_RECONCILIATION')
        self.assertEqual(TerrainStockAdjustment.objects.count(),1)

    def test_cancellation_without_signed_permission_is_retained_without_effect(self):
        self.grant.capabilities['permission_contract']['permissions'].remove('animal_sales.cancel');self.grant.save()
        original=self.sale();self.apply(original)
        self.assertEqual(self.apply(self.cancel(original))['reason_code'],'OFFLINE_PERMISSION_DENIED')
        self.assertEqual(self.lot.stock,17);self.assertTrue(Vente.objects.exists())

    def test_new_cancellation_does_not_reinterpret_an_original_from_legacy_v1(self):
        self.grant.capabilities.pop('permission_contract');self.grant.save()
        original=self.sale();self.assertEqual(self.apply(original)['business_status'],'CONFIRMED')
        grant=OfflineAuthorization.objects.create(membership=self.member,device=self.device,
            capabilities={'can_create_terrain_operation':True},rights_version=self.member.version,
            write_generation=1,issued_at=self.grant.issued_at,expires_at=self.grant.expires_at)
        grant.capabilities['permission_contract']=contract_for(grant,effective_permissions(self.member));grant.save()
        cancellation=self.cancel(original);cancellation['offline_authorization_id']=str(grant.pk)
        self.assertEqual(self.apply(cancellation)['reason_code'],'CANCEL_LEGACY_ORIGINAL')
        self.assertEqual(self.lot.stock,17);self.assertFalse(TerrainStockAdjustment.objects.exists())

    def test_offline_cancellation_arriving_before_original_waits_then_applies_once(self):
        original=self.sale();cancel=self.cancel(original)
        self.assertEqual(self.apply(cancel)['business_status'],'WAITING_DEPENDENCY')
        self.assertEqual(self.lot.stock,20)
        self.assertEqual(self.apply(original)['business_status'],'SUPERSEDED')
        self.assertEqual(self.apply(cancel)['business_status'],'CONFIRMED')
        self.assertEqual(self.lot.stock,20);self.assertEqual(TerrainStockAdjustment.objects.count(),1)

    def test_cancellation_cannot_reference_an_unknown_original(self):
        original=self.sale();self.apply(original)
        cancel=self.cancel(original);cancel['payload']['original_operation_id']='99999999-9999-4999-8999-999999999999'
        self.assertEqual(self.apply(cancel)['reason_code'],'CANCELLATION_ORIGINAL_INVALID')
        self.assertEqual(self.lot.stock,17);self.assertFalse(TerrainStockAdjustment.objects.exists())

    def test_cancel_permission_never_transfers_another_employees_original(self):
        original=self.sale();self.apply(original)
        member=self.paul.memberships.get(exploitation=self.farm)
        MembershipPermission.objects.create(membership=member,code='animal_sales.cancel')
        grant=OfflineAuthorization.objects.create(membership=member,device=self.device,
            capabilities={'can_create_terrain_operation':True},rights_version=member.version,
            write_generation=1,issued_at=self.grant.issued_at,expires_at=self.grant.expires_at)
        grant.capabilities['permission_contract']=contract_for(grant,effective_permissions(member));grant.save()
        data=self.cancel(original);data.update(author_user_id=self.paul.pk,
            author_membership_id=member.pk,offline_authorization_id=str(grant.pk))
        self.assertEqual(self.apply(data)['reason_code'],'CANCELLATION_OTHER_AUTHOR')
        self.assertEqual(self.lot.stock,17);self.assertFalse(TerrainStockAdjustment.objects.exists())

    def test_paid_sale_cancellation_requires_resolution_and_never_moves_cash(self):
        original=self.sale();self.apply(original)
        cash=self.terrain('ENCAISSEMENT',{'client_ref':{'server_id':self.customer.pk},
            'vente_ref':{'local_uuid':original['local_entity_id']},'montant_recu':'30.00','mode':'ESPECES'},2,[original['client_operation_id']])
        self.assertEqual(self.apply(cash)['business_status'],'CONFIRMED')
        result=self.apply(self.cancel(original,3))
        self.assertEqual(result['business_status'],'NEEDS_RECONCILIATION')
        self.assertEqual(result['reason_code'],'SALE_HAS_ACTIVE_ALLOCATIONS')
        self.assertEqual(Payment.objects.get().montant,30);self.assertEqual(self.lot.stock,17)
        self.assertFalse(TerrainStockAdjustment.objects.exists())

    def test_expense_and_removal_cancellation(self):
        for index,(kind,payload) in enumerate([('DEPENSE',{'montant':'12.34','note':'Test'}),('MORTALITE',{'quantite':2})]):
            original=self.terrain(kind,{'lot_ref':self.lot_ref(),**payload},index*2+1)
            self.assertEqual(self.apply(original)['business_status'],'CONFIRMED')
            self.assertEqual(self.apply(self.cancel(original,index*2+2))['business_status'],'CONFIRMED')
        self.assertFalse(Depense.objects.exists());self.assertEqual(self.lot.stock,20)

    def test_egg_sale_cancellation_restores_its_original_collection_stock(self):
        from core.models import CollecteOeufs, VenteOeufs
        from core.egg_services import sync_collection_stock_movement, get_egg_stock
        self.lot.type_production='OEUFS';self.lot.save()
        collection=CollecteOeufs.objects.create(exploitation=self.farm,lot=self.lot,
            created_by=self.jean,collecte_at=timezone.now()-timedelta(hours=1),nombre_collecte=30)
        sync_collection_stock_movement(collection)
        original=self.terrain('VENTE_OEUFS',{'lot_ref':self.lot_ref(),
            'client_ref':{'server_id':self.customer.pk},'conditionnement':'UNITE',
            'nombre_conditionnements':3,'prix_unitaire_conditionnement':'2.00'})
        self.assertEqual(self.apply(original)['business_status'],'CONFIRMED')
        self.assertEqual(get_egg_stock(self.farm,self.lot),27)
        self.assertEqual(self.apply(self.cancel(original))['business_status'],'CONFIRMED')
        self.assertEqual(get_egg_stock(self.farm,self.lot),30)
        self.assertFalse(VenteOeufs.objects.exists());self.assertEqual(VenteOeufs.history.count(),1)

    def test_expense_with_feed_dependency_is_not_silently_compensated(self):
        expense=self.terrain('DEPENSE',{'lot_ref':self.lot_ref(),'montant':'10.00'})
        self.assertEqual(self.apply(expense)['business_status'],'CONFIRMED')
        feed=self.terrain('ALIMENTATION',{'lot_ref':self.lot_ref(),'aliment':'Test',
            'quantite_kg':'1.000','depense_ref':{'local_uuid':expense['local_entity_id']}},2,[expense['client_operation_id']])
        self.assertEqual(self.apply(feed)['business_status'],'CONFIRMED')
        result=self.apply(self.cancel(expense,3))
        self.assertEqual(result['business_status'],'NEEDS_RECONCILIATION')
        self.assertEqual(result['reason_code'],'EXPENSE_HAS_ACTIVE_FEED')
        self.assertTrue(Depense.objects.exists())

    def test_owner_history_without_lot_keeps_filters_and_tenant_isolation(self):
        original = self.sale()
        self.assertEqual(self.apply(original)['business_status'], 'CONFIRMED')
        start = (timezone.now() - timedelta(days=1)).isoformat()
        end = (timezone.now() + timedelta(days=1)).isoformat()
        rows = self.history(author=self.jean.pk, status='CONFIRMED', start=start, end=end)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['operation_id'], original['client_operation_id'])
        self.assertEqual(rows[0]['actor_user_id'], self.jean.pk)
        named = self.history(author=self.jean.username)
        self.assertEqual(len(named), 1)
        self.assertEqual(named[0]['operation_id'], original['client_operation_id'])
        self.assertEqual(self.history(author=self.paul.pk), [])
        self.assertEqual(self.history(status='SUPERSEDED'), [])
        self.assertEqual(self.history(start=end), [])
        self.assertEqual(self.history(end=start), [])
        foreign = type(self.owner).objects.create_user(username='foreign-owner-mission-without-lot')
        client = APIClient()
        client.force_authenticate(foreign)
        self.assertEqual(client.get('/api/history/', {
            'view': 'operations', 'operation': original['client_operation_id'],
        }).data['results'], [])
        client.force_authenticate(self.jean)
        self.assertEqual(client.get('/api/history/?view=operations').status_code, 403)

    @skipUnlessDBFeature('supports_json_field_contains')
    def test_owner_history_filters_dates_status_author_and_farm_isolation(self):
        self.apply(self.sale())
        start=(timezone.now()-timedelta(days=1)).isoformat();end=(timezone.now()+timedelta(days=1)).isoformat()
        self.assertEqual(len(self.history(author=self.jean.pk,status='CONFIRMED',start=start,end=end,lot=self.lot.pk)),1)
        named=self.history(author=self.jean.username,lot=self.lot.nom)
        self.assertEqual(len(named),1);self.assertEqual(named[0]['lots'][0]['nom'],self.lot.nom)
        self.assertEqual(self.history(author=self.paul.pk),[])
        self.assertEqual(self.history(status='SUPERSEDED'),[])
        foreign=type(self.owner).objects.create_user(username='foreign-owner-mission')
        client=APIClient();client.force_authenticate(foreign)
        self.assertEqual(client.get('/api/history/?view=operations').data['results'],[])
        client.force_authenticate(self.jean)
        self.assertEqual(client.get('/api/history/?view=operations').status_code,403)

    @skipUnlessDBFeature('supports_json_field_contains')
    def test_owner_lot_filter_includes_refused_original_using_a_local_lot_reference(self):
        from core.models import TerrainEntityMapping
        purchase=self.terrain('ACHAT',{'nom_lot':'Local lot','espece':self.species.pk,
            'quantite':5,'prix_total':'5.00','prix_unitaire':'1.00'})
        self.assertEqual(self.apply(purchase)['business_status'],'CONFIRMED')
        lot_id=TerrainEntityMapping.objects.get(entity_type='LOT').server_entity_id
        sale=self.sale();sale['local_sequence']=2
        sale['payload'].update(lot_ref={'local_uuid':purchase['local_entity_id']},quantite=6)
        self.assertEqual(self.apply(sale)['reason_code'],'STOCK_INSUFFICIENT')
        rows=self.history(lot=lot_id)
        self.assertIn(sale['client_operation_id'],[row['operation_id'] for row in rows])


class ConcurrentEmployeeCancellationTests(OperationFixture, TransactionTestCase):
    def test_two_employee_cancellations_have_only_one_stock_and_financial_effect(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from django.db import connection, connections
        if connection.vendor != 'postgresql':
            self.skipTest('Real PostgreSQL farm lock required')
        self.customer=Client.objects.create(exploitation=self.farm,nom='Concurrent synthetic')
        self.grant.capabilities['permission_contract']=contract_for(self.grant,effective_permissions(self.member))
        self.grant.save()
        original=SimpleOfflineMissionTests.sale(self)
        self.assertEqual(self.apply(original)['business_status'],'CONFIRMED')
        entries=[SimpleOfflineMissionTests.cancel(self,original,index) for index in (2,3)]
        barrier=Barrier(2)
        def submit(data):
            connections.close_all()
            try:
                barrier.wait(timeout=10)
                response,_=self.send(data)
                return response.status_code,response.data
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            replies=list(pool.map(submit,entries))
        self.assertEqual([reply[0] for reply in replies],[200,200])
        states=[reply[1]['receipts'][0]['business_status'] for reply in replies]
        self.assertEqual(states.count('CONFIRMED'),1)
        self.assertEqual(states.count('NEEDS_RECONCILIATION'),1)
        self.assertEqual(self.lot.stock,20);self.assertEqual(TerrainStockAdjustment.objects.count(),1)
        self.assertFalse(Vente.objects.exists());self.assertEqual(Vente.history.count(),1)
