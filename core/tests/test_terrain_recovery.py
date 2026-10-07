import base64
import copy
import hashlib
import json
import threading
import uuid
from datetime import timedelta
from unittest import skipUnless
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from django.db import connection, connections
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient
from core.models import (User, Client, DeviceChallenge, DeviceRegistration,
    TerrainSubmission, TerrainDecision, AuditEvent)
from core.tests.test_terrain_transport import TransportFixture


class RecoveryFixture(TransportFixture):
    def revoke(self, *, replacement=False):
        self.device.status='REVOKED';self.device.is_primary_writer=False
        self.device.revoked_at=timezone.now();self.device.revocation_reason='SYNTHETIC_REPLACEMENT'
        self.device.save()
        self.grant.revoked_at=timezone.now();self.grant.save()
        if replacement:
            self.farm.write_generation=2;self.farm.save()
            key=ec.generate_private_key(ec.SECP256R1())
            self.new_device=DeviceRegistration.objects.create(exploitation=self.farm,
                installation_uuid=uuid.uuid4(),display_name='Synthetic new writer',status='ACTIVE',
                is_primary_writer=True,write_generation=2,public_key=key.public_key().public_bytes(
                    serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo).decode())

    def client_operation(self, sequence=1):
        operation=self.operation(sequence)
        operation.update(entity_type='CLIENT',payload={'nom':'Client original Jean récupéré'})
        return operation

    def recover(self, operations, *, user=None, challenge=None, key=None, tamper=False,
                reason='Recuperation explicite de declarations conservees sur ancienne tablette.'):
        user=user or self.owner
        challenge=challenge or DeviceChallenge.objects.create(device=self.device,user=user,
            purpose='RECOVER',expires_at=timezone.now()+timedelta(minutes=5))
        data={'reason':reason,'operations':operations}
        body=json.dumps(data,separators=(',',':')).encode()
        path='/api/offline/recovery/'
        message=(f'ELEVAGE-DEVICE-V1\n{challenge.pk}\n{self.device.pk}\n{user.pk}\n'
            f'{challenge.purpose}\nPOST\n{path}\n{hashlib.sha256(body).hexdigest()}').encode()
        signature=(key or self.key).sign(message,ec.ECDSA(hashes.SHA256()))
        if tamper:
            data['reason']+=' Modified after signature.'
            body=json.dumps(data,separators=(',',':')).encode()
        client=APIClient();client.force_authenticate(user)
        response=client.generic('POST',path,body,content_type='application/json',
            HTTP_X_ELEVAGE_DEVICE=str(self.device.pk),HTTP_X_ELEVAGE_CHALLENGE=str(challenge.pk),
            HTTP_X_ELEVAGE_SIGNATURE=base64.b64encode(signature).decode())
        return response,challenge

    def decide(self, operation):
        client=APIClient();client.force_authenticate(self.owner)
        return client.post('/api/offline/reconciliation/'+operation['client_operation_id']+'/',
            {'decision_uuid':str(uuid.uuid4()),'expected_decision_version':0,'action':'APPLY_ORIGINAL',
             'reason':'Pieces et auteur original verifies explicitement.','payload':{}},format='json')


class TerrainRecoveryTests(RecoveryFixture,TestCase):
    def test_recovery_preserves_jean_and_requires_separate_business_decision(self):
        operation=self.client_operation();self.revoke()
        response,_=self.recover([operation]);self.assertEqual(response.status_code,200,response.data)
        received=response.data['receipts'][0]
        self.assertEqual(received['business_status'],'NEEDS_RECONCILIATION')
        self.assertEqual(received['reason_code'],'RECOVERED_REVOKED_DEVICE')
        row=TerrainSubmission.objects.get()
        self.assertEqual(row.author_user_id,self.jean.pk);self.assertEqual(row.payload,operation['payload'])
        self.assertEqual(row.device_generation,1);self.assertEqual(row.offline_authorization,self.grant)
        self.assertEqual(row.local_sequence,operation['local_sequence'])
        self.assertFalse(Client.objects.filter(nom=operation['payload']['nom']).exists())
        self.assertFalse(TerrainDecision.objects.exists());self.assertIsNone(row.outcome.applied_at)
        event=AuditEvent.objects.get(action='TERRAIN_RECOVERED')
        self.assertEqual(event.actor_user_id,self.jean.pk);self.assertEqual(event.decision_actor_id,self.owner.pk)
        self.assertEqual(event.source,'RECOVERY');self.assertEqual(event.operation_id,row.client_operation_id)
        self.assertTrue(event.reason_text);self.device.refresh_from_db()
        self.assertEqual(self.device.status,'REVOKED');self.assertFalse(self.device.is_primary_writer)
        owner=APIClient();owner.force_authenticate(self.owner)
        history=owner.get('/api/audit-events/',{'source':'RECOVERY','decision_actor_id':self.owner.pk})
        self.assertEqual(history.status_code,200,history.data)
        self.assertIn(event.pk,[entry['id'] for entry in history.data['results']])

    def test_uuid_retry_keeps_first_reason_and_changed_original_is_rejected(self):
        operation=self.client_operation();self.revoke()
        first,_=self.recover([operation]);second,_=self.recover([operation],reason='Second delivery of same original.')
        self.assertEqual(first.status_code,200,first.data);self.assertEqual(second.data,first.data)
        self.assertEqual(TerrainSubmission.objects.count(),1)
        self.assertEqual(AuditEvent.objects.filter(action='TERRAIN_RECOVERED').count(),1)
        changed=copy.deepcopy(operation);changed['payload']['nom']='Forged changed original'
        bad,nonce=self.recover([changed]);self.assertEqual(bad.status_code,400,bad.data)
        nonce.refresh_from_db();self.assertIsNone(nonce.consumed_at)
        self.assertEqual(TerrainSubmission.objects.get().payload,operation['payload'])

    def test_nonce_replay_tampering_and_another_key_are_refused(self):
        operation=self.client_operation();self.revoke()
        bad,nonce=self.recover([operation],tamper=True);self.assertEqual(bad.status_code,403,bad.data)
        nonce.refresh_from_db();self.assertIsNone(nonce.consumed_at)
        wrong,_=self.recover([operation],key=ec.generate_private_key(ec.SECP256R1()))
        self.assertEqual(wrong.status_code,403,wrong.data)
        good,_=self.recover([operation],challenge=nonce);self.assertEqual(good.status_code,200,good.data)
        replay,_=self.recover([operation],challenge=nonce);self.assertEqual(replay.status_code,403,replay.data)
        self.assertEqual(TerrainSubmission.objects.count(),1)

    def test_operator_and_foreign_owner_cannot_recover(self):
        operation=self.client_operation();self.revoke()
        delegated=self.member;delegated.can_reconcile=True;delegated.save()
        response,_=self.recover([operation],user=self.jean);self.assertEqual(response.status_code,403,response.data)
        foreign=User.objects.create_user(username='foreign-recovery-owner')
        response,_=self.recover([operation],user=foreign);self.assertEqual(response.status_code,403,response.data)
        self.assertFalse(TerrainSubmission.objects.exists())
        self.assertEqual(APIClient().post('/api/offline/recovery/',{},format='json').status_code,401)

    def test_recovery_challenge_requires_owner_and_revoked_device(self):
        owner=APIClient();owner.force_authenticate(self.owner)
        data={'device_id':self.device.pk,'purpose':'RECOVER'}
        self.assertEqual(owner.post('/api/devices/challenge/',data,format='json').status_code,400)
        self.revoke()
        issued=owner.post('/api/devices/challenge/',data,format='json')
        self.assertEqual(issued.status_code,201,issued.data)
        nonce=DeviceChallenge.objects.get(pk=issued.data['id'])
        self.assertEqual(nonce.purpose,'RECOVER');self.assertEqual(nonce.user,self.owner)
        operator=APIClient();operator.force_authenticate(self.jean)
        self.assertEqual(operator.post('/api/devices/challenge/',data,format='json').status_code,403)
        data['purpose']='WRITE'
        self.assertEqual(owner.post('/api/devices/challenge/',data,format='json').status_code,403)

    def test_recovery_nonce_cannot_authorize_normal_transport_or_write(self):
        operation=self.client_operation();self.revoke()
        nonce=DeviceChallenge.objects.create(device=self.device,user=self.owner,purpose='WRITE',
            expires_at=timezone.now()+timedelta(minutes=5))
        recovery,_=self.recover([operation],challenge=nonce)
        self.assertEqual(recovery.status_code,403,recovery.data)
        normal,_=self.send(operation);self.assertEqual(normal.status_code,403,normal.data)
        self.assertFalse(TerrainSubmission.objects.exists())
        self.device.refresh_from_db();self.assertFalse(self.device.is_primary_writer)

    def test_disabled_original_author_can_be_reviewed_without_reactivation(self):
        operation=self.client_operation();self.revoke()
        self.member.is_active=False;self.member.version+=1;self.member.save()
        self.jean.is_active=False;self.jean.save()
        response,_=self.recover([operation]);self.assertEqual(response.status_code,200,response.data)
        digest=TerrainSubmission.objects.get().declaration_digest
        applied=self.decide(operation);self.assertEqual(applied.status_code,200,applied.data)
        self.assertEqual(applied.data['receipt']['business_status'],'CONFIRMED')
        self.assertEqual(Client.objects.get(nom=operation['payload']['nom']).created_by,self.jean)
        row=TerrainSubmission.objects.get();self.assertEqual(row.declaration_digest,digest)
        self.assertEqual(row.author_user_id,self.jean.pk)
        self.jean.refresh_from_db();self.member.refresh_from_db();self.device.refresh_from_db()
        self.assertFalse(self.jean.is_active);self.assertFalse(self.member.is_active)
        self.assertEqual(self.device.status,'REVOKED');self.assertFalse(self.device.is_primary_writer)
        decision=TerrainDecision.objects.get();self.assertEqual(decision.decision_actor_id,self.owner.pk)

    def test_previously_confirmed_original_is_not_downgraded_or_applied_twice(self):
        operation=self.client_operation();first,_=self.send(operation)
        self.assertEqual(first.status_code,200,first.data)
        self.assertEqual(first.data['receipts'][0]['business_status'],'CONFIRMED')
        self.revoke();restored,_=self.recover([operation])
        self.assertEqual(restored.status_code,200,restored.data)
        self.assertEqual(restored.data['receipts'],first.data['receipts'])
        self.assertEqual(Client.objects.filter(nom=operation['payload']['nom']).count(),1)
        self.assertFalse(AuditEvent.objects.filter(action='TERRAIN_RECOVERED').exists())

    def test_recovery_does_not_replace_new_writer_or_enable_policy(self):
        operation=self.client_operation();self.revoke(replacement=True)
        self.farm.offline_policy_enabled=False;self.farm.save()
        restored,_=self.recover([operation]);self.assertEqual(restored.status_code,200,restored.data)
        self.assertEqual(restored.data['receipts'][0]['business_status'],'NEEDS_RECONCILIATION')
        self.new_device.refresh_from_db();self.farm.refresh_from_db();self.device.refresh_from_db()
        self.assertTrue(self.new_device.is_primary_writer);self.assertEqual(self.new_device.write_generation,2)
        self.assertEqual(self.farm.write_generation,2);self.assertFalse(self.farm.offline_policy_enabled)
        self.assertEqual(self.device.status,'REVOKED');self.assertFalse(self.device.is_primary_writer)
        self.assertEqual(DeviceRegistration.objects.filter(status='ACTIVE',is_primary_writer=True).count(),1)

    def test_bad_second_declaration_rolls_back_entire_batch_and_nonce(self):
        operation=self.client_operation();bad=self.client_operation(2)
        bad['author_user_id']=self.paul.pk;self.revoke()
        response,nonce=self.recover([operation,bad]);self.assertEqual(response.status_code,403,response.data)
        self.assertFalse(TerrainSubmission.objects.exists())
        self.assertFalse(AuditEvent.objects.filter(source='RECOVERY').exists())
        nonce.refresh_from_db();self.assertIsNone(nonce.consumed_at)

    def test_sequence_and_original_grant_context_remain_unique(self):
        operation=self.client_operation();self.revoke()
        response,_=self.recover([operation]);self.assertEqual(response.status_code,200,response.data)
        reused,_=self.recover([self.client_operation()]);self.assertEqual(reused.status_code,400,reused.data)
        forged=self.client_operation(2);forged['device_generation']=2
        response,_=self.recover([forged]);self.assertEqual(response.status_code,403,response.data)
        forged=self.client_operation(2);forged['offline_authorization_id']=str(uuid.uuid4())
        response,_=self.recover([forged]);self.assertEqual(response.status_code,403,response.data)
        self.assertEqual(TerrainSubmission.objects.count(),1)

    def test_reason_expired_challenge_and_inactive_owner_are_refused(self):
        operation=self.client_operation();self.revoke()
        reason,_=self.recover([operation],reason='  ');self.assertEqual(reason.status_code,400,reason.data)
        nonce=DeviceChallenge.objects.create(device=self.device,user=self.owner,purpose='RECOVER',
            expires_at=timezone.now()-timedelta(seconds=1))
        expired,_=self.recover([operation],challenge=nonce);self.assertEqual(expired.status_code,403,expired.data)
        owner_membership=self.owner.memberships.get(exploitation=self.farm)
        owner_membership.is_active=False;owner_membership.save()
        inactive,_=self.recover([operation]);self.assertEqual(inactive.status_code,403,inactive.data)
        self.assertFalse(TerrainSubmission.objects.exists())


@skipUnless(connection.vendor=='postgresql','Real farm/device/nonce locks require PostgreSQL')
class TerrainRecoveryConcurrencyTests(RecoveryFixture,TransactionTestCase):
    def concurrent_recovery(self, operations):
        self.revoke(replacement=True)
        barrier=threading.Barrier(2);results=[];errors=[]
        def worker(operation):
            connections.close_all()
            try:
                barrier.wait(timeout=10)
                result,_=self.recover([operation]);results.append(result.status_code)
            except Exception as error:errors.append(type(error).__name__+': '+str(error))
            finally:connections.close_all()
        threads=[threading.Thread(target=worker,args=(operation,)) for operation in operations]
        for thread in threads:thread.start()
        for thread in threads:thread.join(timeout=20)
        self.assertFalse(any(thread.is_alive() for thread in threads),'Concurrent recovery timed out')
        self.assertEqual(errors,[]);self.assertEqual(len(results),2)
        self.assertEqual(TerrainSubmission.objects.count(),1)
        self.assertEqual(AuditEvent.objects.filter(action='TERRAIN_RECOVERED').count(),1)
        self.assertEqual(DeviceRegistration.objects.filter(status='ACTIVE',is_primary_writer=True).count(),1)
        self.device.refresh_from_db();self.assertEqual(self.device.status,'REVOKED')
        return sorted(results)

    def test_same_uuid_concurrent_recovery_is_idempotent_and_keeps_new_writer(self):
        operation=self.client_operation()
        self.assertEqual(self.concurrent_recovery([operation,copy.deepcopy(operation)]),[200,200])

    def test_competing_uuid_same_sequence_has_one_winner(self):
        self.assertEqual(self.concurrent_recovery([self.client_operation(),self.client_operation()]),[200,400])
