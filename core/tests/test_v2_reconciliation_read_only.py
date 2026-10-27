"""New fine-protocol decisions are forbidden; receipt/application is unchanged."""
import copy
import uuid
from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken
from core.models import (TerrainSubmission, TerrainOutcome, TerrainDecision, TerrainStockAdjustment,
    Vente, Achat, Depense, Payment, Lettrage, EncaissementTerrain, Mouvement, AuditEvent,
    DeviceChallenge, MembershipPermission, Client)
from core.offline_permissions import contract_for
from core.access_policy import effective_permissions
from core.tests.test_terrain_reconciliation import ReconciliationFixture


class V2ReadOnlyTests(ReconciliationFixture, TestCase):
    actions=('APPLY_ORIGINAL','CORRECTION','CANCEL','REVERSE','CASH_ALLOCATION')

    def setUp(self):
        super().setUp()
        self.grant.capabilities['permission_contract']=contract_for(self.grant,effective_permissions(self.member))
        self.grant.save()

    def client_for(self,user):
        client=APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer '+str(AccessToken.for_user(user)))
        return client

    def body(self,operation,action):
        data=self.decision(operation,action)
        if action=='CORRECTION':data['payload']=copy.deepcopy(operation['payload'])
        if action=='CASH_ALLOCATION':
            data['payload']={'vente_ref':{'server_id':1},'vente_type':'VENTE_ANIMAUX','montant':'1.00'}
        return data

    def snapshot(self):
        self.farm.refresh_from_db()
        models=(TerrainSubmission,TerrainOutcome,TerrainDecision,TerrainStockAdjustment,Vente,Achat,
                Depense,Payment,Lettrage,EncaissementTerrain,Mouvement,AuditEvent,DeviceChallenge)
        return (self.farm.business_revision,{model.__name__:list(model._base_manager.order_by('pk').values()) for model in models})

    def assert_blocked(self,client,operation):
        before=self.snapshot()
        for action in self.actions:
            with self.subTest(action=action):
                # Old HTTP body: no new mode/version header or V2 client hint.
                response=client.post('/api/offline/reconciliation/'+operation['client_operation_id']+'/',
                                     self.body(operation,action),format='json')
                self.assertEqual(response.status_code,403,response.data)
                self.assertEqual(str(response.data['detail']),'V2_RECONCILIATION_READ_ONLY')
                self.assertEqual(self.snapshot(),before)

    def test_all_actions_all_roles_old_http_clients_leave_original_and_effects_unchanged(self):
        sale=self.sale();self.assertEqual(self.apply(sale)['business_status'],'CONFIRMED')
        pending=self.sale(100,2);self.assertEqual(self.apply(pending)['business_status'],'NEEDS_RECONCILIATION')
        cash=self.terrain('ENCAISSEMENT',{'client_ref':{'server_id':self.customer.pk},
            'montant_recu':'50.00','mode':'ESPECES'},3)
        self.assertEqual(self.apply(cash)['business_status'],'NEEDS_RECONCILIATION')
        for user in (self.owner,self.jean,self.paul):
            for operation in (sale,pending,cash):
                with self.subTest(user=user.pk,operation=operation['entity_type']):
                    self.assert_blocked(self.client_for(user),operation)
        delegated=self.paul.memberships.get(exploitation=self.farm)
        delegated.can_reconcile=True;delegated.save()
        MembershipPermission.objects.create(membership=delegated,code='reconciliation.decide',created_by=self.owner)
        self.assert_blocked(self.client_for(self.paul),pending)

    def test_malformed_fine_contract_never_becomes_v1_or_enables_a_decision(self):
        self.grant.capabilities['permission_contract']={'version':999};self.grant.save()
        operation=self.sale();self.apply(operation)
        self.assert_blocked(self.client_for(self.owner),operation)
        row=TerrainSubmission.objects.get()
        self.assertEqual(row.outcome.business_status,'NEEDS_RECONCILIATION')
        self.assertFalse(Vente.objects.exists())
        detail=self.client_for(self.owner).get('/api/offline/reconciliation/'+operation['client_operation_id']+'/')
        self.assertEqual(detail.data['reconciliation_mode'],'READ_ONLY_V2')

    def test_authorized_readers_keep_original_result_and_historical_decisions(self):
        operation=self.sale(100);self.apply(operation)
        row=TerrainSubmission.objects.get()
        historical=TerrainDecision.objects.create(exploitation=self.farm,submission=row,
            decision_uuid=uuid.uuid4(),decision_actor_id=self.owner.pk,action='CANCEL',reason='Historical synthetic decision',
            request_digest='a'*64,before_data={},after_data={})
        delegated=self.paul.memberships.get(exploitation=self.farm);delegated.can_reconcile=True;delegated.save()
        for user in (self.owner,self.paul):
            client=self.client_for(user)
            response=client.get('/api/offline/reconciliation/')
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.data['results'][0]['reconciliation_mode'],'READ_ONLY_V2')
            detail=client.get('/api/offline/reconciliation/'+operation['client_operation_id']+'/')
            self.assertEqual(detail.status_code,200)
            self.assertEqual(detail.data['original_payload'],operation['payload'])
            self.assertEqual(detail.data['original_author_id'],self.jean.pk)
            self.assertEqual(str(detail.data['decisions'][0]['decision_uuid']),str(historical.decision_uuid))
        # Even a historical UUID is not a route for a new fine-protocol command.
        self.assert_blocked(self.client_for(self.owner),operation)
        self.assertEqual(self.client_for(self.jean).get('/api/offline/reconciliation/').status_code,403)

    def test_automatic_v2_application_needs_no_owner_or_reconciliation_request(self):
        operation=self.sale()
        response,_=self.send(operation)
        self.assertEqual(response.status_code,200,response.data)
        self.assertEqual(response.data['receipts'][0]['business_status'],'CONFIRMED')
        self.send(operation)
        self.assertEqual(Vente.objects.count(),1)
        self.assertEqual(Vente.objects.get().created_by_id,self.jean.pk)
        self.assertEqual(self.lot.stock,17)
        self.assertFalse(TerrainDecision.objects.exists())
        self.assertFalse(AuditEvent.objects.filter(source='RECONCILE').exists())

    def test_v1_read_mode_and_existing_cancellation_remain_available(self):
        self.grant.capabilities.pop('permission_contract');self.grant.save()
        operation=self.sale(100);self.apply(operation)
        client=self.client_for(self.owner)
        path='/api/offline/reconciliation/'+operation['client_operation_id']+'/'
        self.assertEqual(client.get(path).data['reconciliation_mode'],'LEGACY_V1')
        data=self.decision(operation,'CANCEL')
        self.assertEqual(client.post(path,data,format='json').status_code,200)
        self.assertEqual(client.post(path,data,format='json').status_code,200)
        self.assertEqual(TerrainDecision.objects.count(),1)
