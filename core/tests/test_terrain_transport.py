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
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import connection, connections, transaction, DatabaseError
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient
from core.models import (User, DeviceRegistration, DeviceTransportChallenge, OfflineAuthorization,
    TerrainSubmission, TerrainOutcome, AuditEvent)


class TransportFixture:
    def setUp(self):
        cache.clear()
        self.owner=User.objects.create_user(username='transport-owner')
        self.farm=self.owner.exploitation
        self.farm.offline_policy_enabled=True;self.farm.save()
        self.jean=User.objects.create_user(username='transport-jean',exploitation=self.farm)
        self.paul=User.objects.create_user(username='transport-paul',exploitation=self.farm)
        self.member=self.jean.memberships.get(exploitation=self.farm)
        self.key=ec.generate_private_key(ec.SECP256R1())
        self.device=DeviceRegistration.objects.create(exploitation=self.farm,installation_uuid=uuid.uuid4(),
            display_name='Synthetic transport',status='ACTIVE',is_primary_writer=True,write_generation=1,
            public_key=self.key.public_key().public_bytes(serialization.Encoding.PEM,
                serialization.PublicFormat.SubjectPublicKeyInfo).decode())
        self.grant=OfflineAuthorization.objects.create(membership=self.member,device=self.device,
            rights_version=1,write_generation=1,capabilities={'can_create_terrain_operation':True},
            issued_at=timezone.now()-timedelta(minutes=1),expires_at=timezone.now()+timedelta(days=3))

    def operation(self,sequence=1):
        return {'client_operation_id':str(uuid.uuid4()),'local_sequence':sequence,
            'author_user_id':self.jean.pk,'author_membership_id':self.member.pk,
            'device_id':self.device.pk,'device_generation':1,'exploitation_id':self.farm.pk,
            'offline_authorization_id':str(self.grant.pk),'entity_type':'CLIENT','operation_type':'CREATE',
            'local_entity_id':str(uuid.uuid4()),'payload':{'nom':'Client terrain synthétique'},'dependencies':[],
            'business_occurred_at':timezone.now().isoformat(),'local_recorded_at':timezone.now().isoformat()}

    def send(self,operation,*,purpose='RECEIVE',client=None,challenge=None,tamper=False,key=None):
        client=client or APIClient()
        challenge=challenge or DeviceTransportChallenge.objects.create(device=self.device,purpose=purpose,
            expires_at=timezone.now()+timedelta(minutes=5))
        path='/api/offline/submissions/' if purpose=='RECEIVE' else '/api/offline/submissions/status/'
        data={'operations':[operation]} if purpose=='RECEIVE' else {'operation_ids':[operation['client_operation_id']]}
        body=json.dumps(data,separators=(',',':')).encode()
        message=(f'ELEVAGE-DEVICE-TRANSPORT-V1\n{challenge.pk}\n{self.device.pk}\n{self.farm.pk}\n'
            f'1\n{purpose}\nPOST\n{path}\n{hashlib.sha256(body).hexdigest()}').encode()
        signature=(key or self.key).sign(message,ec.ECDSA(hashes.SHA256()))
        if tamper:body=body.replace(b'Client terrain',b'Changed client')
        response=client.generic('POST',path,body,content_type='application/json',
            HTTP_AUTHORIZATION='Bearer deliberately-expired-personal-session',
            HTTP_X_ELEVAGE_DEVICE=str(self.device.pk),HTTP_X_ELEVAGE_CHALLENGE=str(challenge.pk),
            HTTP_X_ELEVAGE_SIGNATURE=base64.b64encode(signature).decode())
        return response,challenge


class TerrainTransportTests(TransportFixture,TestCase):
    def test_device_transport_preserves_jean_even_when_paul_is_opened(self):
        client=APIClient();client.force_authenticate(self.paul)
        response,_=self.send(self.operation(),client=client)
        self.assertEqual(response.status_code,200,response.data)
        row=TerrainSubmission.objects.get()
        self.assertEqual(row.author_user_id,self.jean.pk)
        self.assertEqual(row.outcome.business_status,'UNREVIEWED')
        self.assertEqual(response.data['receipts'][0]['transport_status'],'SERVER_RECEIVED')
        self.assertIsNone(row.outcome.applied_at)
        event=AuditEvent.objects.get(action='TERRAIN_RECEIVED')
        self.assertEqual(event.actor_user_id,self.jean.pk)
        self.assertEqual(event.transport_identity,'DEVICE')

    def test_lost_response_retry_creates_one_declaration_and_one_receipt_audit(self):
        operation=self.operation()
        first,_=self.send(operation);second,_=self.send(operation)
        self.assertEqual(first.status_code,200,first.data)
        self.assertEqual(second.data,first.data)
        self.assertEqual(TerrainSubmission.objects.count(),1)
        self.assertEqual(TerrainOutcome.objects.count(),1)
        self.assertEqual(AuditEvent.objects.filter(action='TERRAIN_RECEIVED').count(),1)

    def test_uuid_cannot_be_reused_with_another_payload(self):
        operation=self.operation();self.send(operation)
        operation['payload']['nom']='Autre déclaration'
        response,_=self.send(operation)
        self.assertEqual(response.status_code,400)
        self.assertEqual(TerrainSubmission.objects.get().payload['nom'],'Client terrain synthétique')

    def test_device_sequence_is_unique(self):
        self.send(self.operation())
        response,_=self.send(self.operation())
        self.assertEqual(response.status_code,400)
        self.assertEqual(TerrainSubmission.objects.count(),1)

    def test_signature_tampering_and_challenge_replay_are_refused(self):
        operation=self.operation()
        bad,challenge=self.send(operation,tamper=True)
        self.assertEqual(bad.status_code,403)
        challenge.refresh_from_db();self.assertIsNone(challenge.consumed_at)
        good,_=self.send(operation,challenge=challenge);self.assertEqual(good.status_code,200,good.data)
        replay,_=self.send(operation,challenge=challenge);self.assertEqual(replay.status_code,403)

    def test_cross_farm_and_original_author_forgery_are_refused(self):
        operation=self.operation();operation['exploitation_id']+=1000
        response,_=self.send(operation);self.assertEqual(response.status_code,403)
        operation=self.operation();operation['author_user_id']=self.paul.pk
        response,_=self.send(operation);self.assertEqual(response.status_code,403)
        self.assertFalse(TerrainSubmission.objects.exists())

    def test_disabled_original_author_is_retained_for_reconciliation(self):
        self.member.is_active=False;self.member.version+=1;self.member.save()
        self.jean.is_active=False;self.jean.save()
        response,_=self.send(self.operation())
        self.assertEqual(response.status_code,200,response.data)
        self.assertEqual(response.data['receipts'][0]['business_status'],'NEEDS_RECONCILIATION')
        self.assertEqual(TerrainSubmission.objects.get().author_user_id,self.jean.pk)

    def test_expired_grant_keeps_operations_created_during_its_original_window(self):
        self.grant.issued_at=timezone.now()-timedelta(days=4)
        self.grant.expires_at=timezone.now()-timedelta(days=1);self.grant.save()
        operation=self.operation();operation['local_recorded_at']=(self.grant.issued_at+timedelta(hours=1)).isoformat()
        response,_=self.send(operation)
        self.assertEqual(response.status_code,200,response.data)
        self.assertEqual(response.data['receipts'][0]['business_status'],'UNREVIEWED')

    def test_dependencies_are_received_without_pretending_business_confirmation(self):
        operation=self.operation();operation['dependencies']=[str(uuid.uuid4())]
        response,_=self.send(operation)
        self.assertEqual(response.status_code,200,response.data)
        self.assertEqual(response.data['receipts'][0]['business_status'],'WAITING_DEPENDENCY')
        self.assertEqual(TerrainSubmission.objects.get().dependencies,operation['dependencies'])

    def test_revoked_device_cannot_send_old_declarations_through_normal_transport(self):
        operation=self.operation();self.device.status='REVOKED';self.device.is_primary_writer=False;self.device.save()
        response,_=self.send(operation);self.assertEqual(response.status_code,403)
        self.assertFalse(TerrainSubmission.objects.exists())

    def test_payload_cannot_contain_jwt_or_personal_secrets(self):
        operation=self.operation();operation['payload']['nested']={'refresh_token':'synthetic-secret'}
        response,_=self.send(operation);self.assertEqual(response.status_code,400)
        self.assertFalse(TerrainSubmission.objects.exists())

    def test_declaration_orm_is_immutable_but_outcome_is_separate(self):
        self.send(self.operation());row=TerrainSubmission.objects.get()
        row.payload={'nom':'changed'}
        with self.assertRaises(ValidationError):row.save()
        with self.assertRaises(ValidationError):TerrainSubmission.objects.all().update(payload={})
        with self.assertRaises(ValidationError):row.delete()
        with self.assertRaises(ValidationError):TerrainSubmission.objects.all().delete()
        outcome=row.outcome;outcome.business_status='NEEDS_RECONCILIATION';outcome.save()
        row.refresh_from_db();self.assertEqual(row.payload['nom'],'Client terrain synthétique')

    @skipUnless(connection.vendor=='postgresql','PostgreSQL raw SQL protection')
    def test_postgresql_declaration_update_and_delete_are_refused(self):
        self.send(self.operation());row=TerrainSubmission.objects.get()
        for sql in ['UPDATE core_terrainsubmission SET payload=payload WHERE id=%s',
                    'DELETE FROM core_terrainsubmission WHERE id=%s']:
            with self.assertRaises(DatabaseError) as error:
                with transaction.atomic():
                    with connection.cursor() as cursor:cursor.execute(sql,[row.pk])
            self.assertEqual(getattr(error.exception.__cause__,'sqlstate',getattr(error.exception.__cause__,'pgcode',None)),'P0001')
        self.assertEqual(TerrainSubmission.objects.count(),1)


@skipUnless(connection.vendor=='postgresql','Real PostgreSQL locking required')
class TerrainConcurrentReceiptTests(TransportFixture,TransactionTestCase):
    def test_two_simultaneous_receipts_of_same_uuid_have_one_effect(self):
        operation=self.operation();barrier=threading.Barrier(2);results=[];errors=[]
        def worker():
            connections.close_all()
            try:
                barrier.wait(timeout=10)
                response,_=self.send(copy.deepcopy(operation))
                results.append((response.status_code,response.data))
            except Exception as error:errors.append(error)
            finally:connections.close_all()
        threads=[threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:thread.start()
        for thread in threads:thread.join(timeout=20)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertFalse(errors,errors)
        self.assertEqual([status for status,_ in results],[200,200],results)
        self.assertEqual(TerrainSubmission.objects.count(),1)
        self.assertEqual(TerrainOutcome.objects.count(),1)
        self.assertEqual(AuditEvent.objects.filter(action='TERRAIN_RECEIVED').count(),1)
