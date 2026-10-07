import copy
import threading
import uuid
from decimal import Decimal
from unittest import skipUnless
from django.db import connection, connections, transaction, DatabaseError
from django.test import TestCase, TransactionTestCase
from rest_framework.test import APIClient
from core.models import (User, Client, Vente, Payment, Lettrage, EncaissementTerrain,
    TerrainSubmission, TerrainDecision, AuditEvent)
from core.tests.test_terrain_operations import OperationFixture


class ReconciliationFixture(OperationFixture):
    def setUp(self):
        super().setUp()
        self.customer=Client.objects.create(exploitation=self.farm, nom='Client test')

    def sale(self, quantity=3, sequence=1):
        return self.terrain('VENTE_ANIMAUX', {'lot_ref':self.lot_ref(),
            'client_ref':{'server_id':self.customer.pk}, 'quantite':quantity, 'prix_unitaire':'10000.00'}, sequence)

    def decision(self, operation, action='APPLY_ORIGINAL', version=0, payload=None):
        return {'decision_uuid':str(uuid.uuid4()), 'expected_decision_version':version,
            'action':action, 'reason':'Inventaire et pièces vérifiés.', 'payload':payload or {}}

    def submit_decision(self, operation, data=None, user=None):
        client=APIClient();client.force_authenticate(user or self.owner)
        return client.post('/api/offline/reconciliation/'+operation['client_operation_id']+'/',
            data or self.decision(operation), format='json')


class ReconciliationTests(ReconciliationFixture, TestCase):
    def test_disabled_original_author_can_be_explicitly_decided_without_reactivation_or_transfer(self):
        operation=self.terrain('CLIENT',{'nom':'Client original'})
        self.member.is_active=False;self.member.save()
        self.jean.is_active=False;self.jean.save()
        self.assertEqual(self.apply(operation)['business_status'],'NEEDS_RECONCILIATION')
        row=TerrainSubmission.objects.get();digest=row.declaration_digest
        data=self.decision(operation)
        result=self.submit_decision(operation,data)
        self.assertEqual(result.status_code,200,result.data)
        self.assertEqual(result.data['receipt']['business_status'],'CONFIRMED')
        self.assertEqual(Client.objects.get(nom='Client original').created_by,self.jean)
        self.assertEqual(self.submit_decision(operation,data).status_code,200)
        self.assertEqual(TerrainDecision.objects.count(),1)
        row.refresh_from_db();self.assertEqual(row.payload,operation['payload']);self.assertEqual(row.declaration_digest,digest)
        self.member.refresh_from_db();self.jean.refresh_from_db();self.assertFalse(self.member.is_active);self.assertFalse(self.jean.is_active)
        event=AuditEvent.objects.get(action='APPLY_ORIGINAL',entity_type='core.terrainsubmission')
        self.assertEqual(event.actor_user_id,self.jean.pk);self.assertEqual(event.decision_actor_id,self.owner.pk)
        self.assertEqual(event.source,'RECONCILE');self.assertEqual(event.before_data['business_status'],'NEEDS_RECONCILIATION')
        self.assertEqual(event.after_data['business_status'],'CONFIRMED');self.assertTrue(event.reason_text)
        self.assertEqual(AuditEvent.objects.filter(decision_actor_id=self.owner.pk).values('correlation_id').distinct().count(),1)

    def test_cancel_unapplied_retains_original_and_reason(self):
        operation=self.sale(21);self.apply(operation)
        result=self.submit_decision(operation,self.decision(operation,'CANCEL'))
        self.assertEqual(result.status_code,200,result.data)
        self.assertEqual(result.data['receipt']['business_status'],'NOT_APPLIED')
        self.assertEqual(self.lot.stock,20);self.assertFalse(Vente.objects.exists())
        self.assertEqual(TerrainSubmission.objects.get().payload['quantite'],21)
        self.assertTrue(TerrainDecision.objects.get().reason)

    def test_correction_is_separate_and_cannot_force_negative_stock(self):
        operation=self.sale(21);self.apply(operation)
        refused=self.submit_decision(operation)
        self.assertEqual(refused.status_code,400);self.assertEqual(self.lot.stock,20);self.assertFalse(TerrainDecision.objects.exists())
        corrected=dict(operation['payload']);corrected['quantite']=2
        result=self.submit_decision(operation,self.decision(operation,'CORRECTION',payload=corrected))
        self.assertEqual(result.status_code,200,result.data)
        self.assertEqual(self.lot.stock,18);self.assertEqual(Vente.objects.get().quantite,2)
        self.assertEqual(TerrainSubmission.objects.get().payload['quantite'],21)
        self.assertEqual(TerrainDecision.objects.get().effective_payload['quantite'],2)

    def test_applied_sale_cannot_be_cancelled_or_reapplied(self):
        operation=self.sale();self.apply(operation)
        for action in ('CANCEL','APPLY_ORIGINAL'):
            result=self.submit_decision(operation,self.decision(operation,action))
            self.assertEqual(result.status_code,400,result.data)
        self.assertEqual(self.lot.stock,17);self.assertEqual(Vente.objects.count(),1);self.assertFalse(TerrainDecision.objects.exists())

    def test_decision_version_and_uuid_reuse_are_refused_without_new_effect(self):
        operation=self.sale(21);self.apply(operation)
        data=self.decision(operation,'CANCEL');self.assertEqual(self.submit_decision(operation,data).status_code,200)
        stale=self.decision(operation,'CANCEL');self.assertEqual(self.submit_decision(operation,stale).status_code,400)
        altered=copy.deepcopy(data);altered['reason']='Autre motif'
        self.assertEqual(self.submit_decision(operation,altered).status_code,400)
        self.assertEqual(TerrainDecision.objects.count(),1)

    def test_reason_secrets_unknown_fields_and_unauthorized_operator_are_refused(self):
        operation=self.sale(21);self.apply(operation)
        data=self.decision(operation)
        self.assertEqual(self.submit_decision(operation,data,self.paul).status_code,403)
        for key,value in [('reason',' '),('payload',{'access_token':'must not persist'}),('arbitrary','value')]:
            invalid=copy.deepcopy(data);invalid[key]=value
            self.assertEqual(self.submit_decision(operation,invalid).status_code,400)
        self.assertFalse(TerrainDecision.objects.exists())

    def test_delegated_operator_requires_active_primary_device_proof(self):
        operation=self.sale(21);self.apply(operation)
        member=self.paul.memberships.get();member.can_reconcile=True;member.save()
        self.assertEqual(self.submit_decision(operation,user=self.paul).status_code,403)
        self.assertFalse(TerrainDecision.objects.exists())

    def test_other_farm_owner_cannot_read_or_decide_original(self):
        operation=self.sale(21);self.apply(operation)
        other=User.objects.create_user(username='other-reconciliation-owner')
        self.assertEqual(self.submit_decision(operation,user=other).status_code,404)
        client=APIClient();client.force_authenticate(other)
        self.assertEqual(client.get('/api/offline/reconciliation/').data['count'],0)
        self.assertEqual(client.get('/api/offline/reconciliation/'+operation['client_operation_id']+'/').status_code,404)

    def test_supervision_lists_only_server_received_and_preserves_decision_timeline(self):
        operation=self.sale(21);self.apply(operation)
        client=APIClient();client.force_authenticate(self.owner)
        response=client.get('/api/offline/reconciliation/')
        self.assertEqual(response.status_code,200);self.assertEqual(response.data['count'],1)
        self.assertEqual(response.data['results'][0]['original_author_id'],self.jean.pk)
        self.submit_decision(operation,self.decision(operation,'CANCEL'))
        detail=client.get('/api/offline/reconciliation/'+operation['client_operation_id']+'/').data
        self.assertEqual(len(detail['decisions']),1);self.assertEqual(detail['original_payload']['quantite'],21)
        self.assertEqual(client.get('/api/offline/reconciliation/').data['count'],0)
        self.assertEqual(client.get('/api/offline/reconciliation/?state=ALL').data['count'],1)

    def test_cash_remainder_allocates_explicitly_and_exactly_without_rewriting_physical_receipt(self):
        sale=self.sale();self.apply(sale)
        cash=self.terrain('ENCAISSEMENT',{'client_ref':{'server_id':self.customer.pk},
            'vente_ref':{'local_uuid':sale['local_entity_id']},'montant_recu':'50000.00','mode':'ESPECES'},2,[sale['client_operation_id']])
        self.apply(cash)
        second=self.sale(2,3);self.apply(second)
        payload={'vente_ref':{'local_uuid':second['local_entity_id']},'vente_type':'VENTE_ANIMAUX','montant':'20000.00'}
        data=self.decision(cash,'CASH_ALLOCATION',payload=payload)
        result=self.submit_decision(cash,data)
        self.assertEqual(result.status_code,200,result.data)
        self.assertEqual(result.data['receipt']['business_status'],'CONFIRMED')
        self.assertEqual(result.data['receipt']['cash_recognition']['montant_recu'],'50000.00')
        self.assertEqual(result.data['receipt']['cash_recognition']['montant_a_rapprocher'],'0.00')
        self.assertEqual(Payment.objects.get().montant,Decimal('50000.00'))
        recognized=EncaissementTerrain.objects.get();self.assertEqual(recognized.created_by,self.jean)
        self.assertEqual(recognized.montant_affecte,Decimal('50000.00'))
        self.assertEqual(self.submit_decision(cash,data).status_code,200)
        self.assertEqual(Lettrage.objects.count(),2);self.assertEqual(Payment.objects.count(),1)
        self.assertTrue(AuditEvent.objects.filter(entity_type='core.lettrage',decision_actor_id=self.owner.pk,actor_user_id=self.jean.pk).exists())

    def test_cash_allocation_cannot_exceed_debt_and_receipt(self):
        cash=self.terrain('ENCAISSEMENT',{'client_ref':{'server_id':self.customer.pk},'montant_recu':'13.01','mode':'ESPECES'})
        self.apply(cash);sale=self.sale(1,2);self.apply(sale)
        data=self.decision(cash,'CASH_ALLOCATION',payload={'vente_ref':{'local_uuid':sale['local_entity_id']},'vente_type':'VENTE_ANIMAUX','montant':'13.02'})
        self.assertEqual(self.submit_decision(cash,data).status_code,400)
        self.assertEqual(EncaissementTerrain.objects.get().montant_a_rapprocher,Decimal('13.01'))
        self.assertFalse(Lettrage.objects.exists());self.assertFalse(TerrainDecision.objects.exists())

    def test_activity_filters_connect_original_author_decider_and_object_timeline(self):
        operation=self.sale(21);self.apply(operation)
        self.submit_decision(operation,self.decision(operation,'CANCEL'))
        client=APIClient();client.force_authenticate(self.owner)
        response=client.get('/api/audit-events/',{'operation_id':operation['client_operation_id'],
            'actor_user_id':self.jean.pk,'decision_actor_id':self.owner.pk,'source':'RECONCILE'})
        self.assertEqual(response.status_code,200,response.data)
        self.assertGreaterEqual(response.data['count'],2)
        self.assertTrue(all(row['operation_id']==operation['client_operation_id'] for row in response.data['results']))
        self.assertTrue(all(row['decision_actor_id']==self.owner.pk for row in response.data['results']))
        self.assertEqual(len({row['correlation_id'] for row in response.data['results']}),1)
        self.assertEqual(client.get('/api/audit-events/?decision_actor_id=invalid').status_code,400)
        self.assertEqual(client.get('/api/audit-events/?since=2026-01-02T00:00:00Z&until=2026-01-01T00:00:00Z').status_code,400)
        client.force_authenticate(self.jean)
        self.assertEqual(client.get('/api/audit-events/').status_code,403)

    def test_cash_allocation_refuses_an_inconsistent_legacy_allocation_ledger(self):
        cash=self.terrain('ENCAISSEMENT',{'client_ref':{'server_id':self.customer.pk},'montant_recu':'13.01','mode':'ESPECES'})
        self.apply(cash);sale=self.sale(1,2);self.apply(sale)
        Lettrage.objects.create(payment=Payment.objects.get(),vente=Vente.objects.get(),montant=Decimal('1.00'))
        data=self.decision(cash,'CASH_ALLOCATION',payload={'vente_ref':{'local_uuid':sale['local_entity_id']},'vente_type':'VENTE_ANIMAUX','montant':'13.01'})
        response=self.submit_decision(cash,data)
        self.assertEqual(response.status_code,400,response.data)
        self.assertEqual(str(response.data[0]),'CASH_ALLOCATION_LEDGER_MISMATCH')
        self.assertEqual(Lettrage.objects.count(),1);self.assertFalse(TerrainDecision.objects.exists())

    @skipUnless(connection.vendor=='postgresql','Actual PostgreSQL decision trigger required')
    def test_postgresql_decision_update_delete_refused_in_raw_sql(self):
        operation=self.sale(21);self.apply(operation);self.submit_decision(operation,self.decision(operation,'CANCEL'))
        pk=TerrainDecision.objects.get().pk
        for sql in ['UPDATE core_terraindecision SET reason=%s WHERE id=%s','DELETE FROM core_terraindecision WHERE id=%s']:
            with self.assertRaises(DatabaseError) as error:
                with transaction.atomic(),connection.cursor() as cursor:
                    cursor.execute(sql,['changed',pk] if sql.startswith('UPDATE') else [pk])
            self.assertEqual(getattr(error.exception.__cause__,'sqlstate',None) or getattr(error.exception.__cause__,'pgcode',None),'P0001')


@skipUnless(connection.vendor=='postgresql','Actual PostgreSQL decision concurrency required')
class ConcurrentDecisionTests(ReconciliationFixture,TransactionTestCase):
    def test_two_decisions_on_same_version_apply_at_most_one_effect(self):
        operation=self.terrain('CLIENT',{'nom':'Client reçu après désactivation'})
        self.member.is_active=False;self.member.save();self.apply(operation)
        barrier=threading.Barrier(2);results=[];errors=[]
        def decide():
            connections.close_all()
            try:
                barrier.wait(timeout=10)
                result=self.submit_decision(operation,self.decision(operation))
                results.append(result.status_code)
            except Exception as error:errors.append(repr(error))
            finally:connections.close_all()
        threads=[threading.Thread(target=decide) for _ in range(2)]
        for thread in threads:thread.start()
        for thread in threads:thread.join(timeout=20);self.assertFalse(thread.is_alive())
        self.assertEqual(errors,[]);self.assertCountEqual(results,[200,400])
        self.assertEqual(TerrainDecision.objects.count(),1)
        self.assertEqual(Client.objects.filter(nom=operation['payload']['nom']).count(),1)
