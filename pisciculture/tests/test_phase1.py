import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch
from django.db import connection, close_old_connections, transaction, DatabaseError
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient
from core.models import User, Espece, ExploitationMembership, MembershipPermission, Lot, Depense, Vente, Payment
from core.access_policy import permission_contract
from core.permission_catalog import AQUATIC_PERMISSIONS
from pisciculture.models import Activity, Installation, AquaticSpeciesProfile, Cohort, Position, Command, Event, CountLine
from pisciculture.services import execute

PREFIX='/api/pisciculture/v1/'


class Fixture:
    def setUp(self):
        self.owner=User.objects.create_user(username='aquatic-owner',password='synthetic-local-password')
        self.farm=self.owner.exploitation
        self.api=APIClient();self.api.force_authenticate(self.owner)
        Activity.objects.create(exploitation=self.farm,enabled=True)
        self.species=Espece.objects.create(exploitation=self.farm,nom='Tilapia')
        AquaticSpeciesProfile.objects.create(exploitation=self.farm,species=self.species)
        self.a=Installation.objects.create(exploitation=self.farm,code='A',name='Nord',type='BASSIN')
        self.b=Installation.objects.create(exploitation=self.farm,code='B',name='Sud',type='BASSIN')
        self.at=(timezone.now()-timezone.timedelta(hours=2)).isoformat()
        self.created=self.post('cohorts/',id=str(uuid.uuid4()),code='C',name='Novembre',species=self.species.pk,
            stage=None,origin={'type':'PURCHASE','reference':None},started_at=self.at,initial_count=1000,
            allocations=[{'installation':str(self.a.pk),'count':600},{'installation':str(self.b.pk),'count':400}],reason='Initial')
        self.assertEqual(self.created.status_code,201,self.created.data)
        self.cohort=Cohort.objects.get(pk=self.created.data['id'])

    def post(self,path,**body):
        return self.api.post(PREFIX+path,{'operation_uuid':str(uuid.uuid4()),'schema_version':1,**body},format='json')

    def transfer(self,count=150):
        return {'operation_uuid':str(uuid.uuid4()),'schema_version':1,'cohort':str(self.cohort.pk),
            'source':str(self.a.pk),'destination':str(self.b.pk),'count':count,
            'occurred_at':timezone.now().isoformat(),'reason':'Répartition',
            'expected_versions':{'cohort':1,'source_position':1,'destination_position':1}}


class Phase1Tests(Fixture,TestCase):
    def test_1000_and_transfer_150_and_replay(self):
        self.assertEqual(self.api.get(PREFIX+'dashboard/').data['metrics']['live_count']['value'],1000)
        body=self.transfer();r=self.api.post(PREFIX+'transfers/',body,format='json')
        self.assertEqual(r.status_code,201,r.data)
        self.assertEqual([p['gross_count'] for p in r.data['positions']],[450,550])
        r2=self.api.post(PREFIX+'transfers/',body,format='json')
        self.assertEqual(r2.data,r.data);self.assertEqual(r2['Idempotent-Replay'],'true')
        self.assertEqual(Event.objects.count(),2);self.assertEqual(CountLine.objects.count(),4)
        body['count']=151
        self.assertEqual(self.api.post(PREFIX+'transfers/',body,format='json').status_code,409)

    def test_validation_versions_and_negative(self):
        body=self.transfer(601)
        self.assertEqual(self.api.post(PREFIX+'transfers/',body,format='json').status_code,409)
        for value in [0,-1,1.5,True,'150']:
            body=self.transfer();body['count']=value
            self.assertEqual(self.api.post(PREFIX+'transfers/',body,format='json').status_code,400)
        body=self.transfer();body['expected_versions']['cohort']=99
        self.assertEqual(self.api.post(PREFIX+'transfers/',body,format='json').status_code,409)
        self.assertEqual(Event.objects.count(),1)

    def test_no_financial_or_terrestrial_records_created(self):
        for model in [Lot,Depense,Vente,Payment]:self.assertEqual(model.objects.count(),0)
        before=self.api.get('/api/dashboard/').data
        self.assertEqual(before['kpis']['stock'],0)
        self.api.post(PREFIX+'transfers/',self.transfer(),format='json')
        self.assertEqual(self.api.get('/api/dashboard/').data,before)

    def test_multi_cohort_no_double_count(self):
        r=self.post('cohorts/',id=str(uuid.uuid4()),code='D',name='Deuxième',species=self.species.pk,
            origin={'type':'HATCH'},started_at=self.at,initial_count=100,allocations=[{'installation':str(self.a.pk),'count':100}],reason='Initial')
        self.assertEqual(r.status_code,201,r.data)
        d=self.api.get(PREFIX+'dashboard/').data
        self.assertEqual(d['metrics']['live_count']['value'],1100)
        self.assertEqual(d['metrics']['active_cohorts']['value'],2)

    def test_tenant_details_foreign_references_filters(self):
        other=User.objects.create_user(username='other');Activity.objects.create(exploitation=other.exploitation,enabled=True)
        self.api.force_authenticate(other)
        for path in [f'cohorts/{self.cohort.pk}/',f'installations/{self.a.pk}/']:
            self.assertEqual(self.api.get(PREFIX+path).status_code,404)
        self.assertEqual(self.api.get(PREFIX+'events/',{'cohort':str(self.cohort.pk)}).data['results'],[])
        self.assertEqual(self.api.post(PREFIX+'transfers/',self.transfer(),format='json').status_code,404)
        self.assertEqual(self.api.get(PREFIX+'cohorts/').data['results'],[])

    def test_operator_grant_and_revocation_and_legacy_contract(self):
        user=User.objects.create_user(username='operator',exploitation=self.farm)
        member=ExploitationMembership.objects.get(user=user)
        self.api.force_authenticate(user)
        self.assertEqual(self.api.get(PREFIX+'dashboard/').status_code,403)
        for code in AQUATIC_PERMISSIONS:MembershipPermission.objects.create(membership=member,code=code,created_by=self.owner)
        self.assertEqual(self.api.get(PREFIX+'dashboard/').status_code,200)
        self.assertEqual(self.api.post(PREFIX+'transfers/',self.transfer(),format='json').status_code,201)
        self.assertFalse(set(permission_contract(user)['permissions']) & AQUATIC_PERMISSIONS)
        self.assertFalse(set(permission_contract(self.owner,version=2)['permissions']) & AQUATIC_PERMISSIONS)
        member.is_active=False;member.save()
        self.assertEqual(self.api.get(PREFIX+'dashboard/').status_code,403)

    def test_aquatic_permissions_never_enter_signed_offline_grants(self):
        from types import SimpleNamespace
        from core.offline_permissions import contract_for
        member=ExploitationMembership.objects.get(user=self.owner)
        grant=SimpleNamespace(pk=uuid.uuid4(),membership=member,device_id=1,write_generation=1,
            rights_version=1,issued_at=timezone.now(),expires_at=timezone.now()+timezone.timedelta(days=1))
        result=contract_for(grant,['finance.read','aquatic.read','aquatic.transfers.create'])
        self.assertEqual(result['permissions'],['finance.read'])

    def test_module_disabled_and_explicit_activation(self):
        Activity.objects.filter(exploitation=self.farm).update(enabled=False)
        self.assertFalse(self.api.get(PREFIX+'capabilities/').data['enabled'])
        self.assertEqual(self.api.get(PREFIX+'dashboard/').status_code,403)
        self.assertEqual(self.post('activation/',enabled=True).status_code,201)
        self.assertEqual(self.api.get(PREFIX+'dashboard/').status_code,200)
        self.assertEqual(self.api.get(PREFIX+'capabilities/').data['offline_write'],[])

    def test_offline_policy_operator_requires_existing_device_proof(self):
        user=User.objects.create_user(username='secured-operator',exploitation=self.farm)
        member=ExploitationMembership.objects.get(user=user)
        for code in AQUATIC_PERMISSIONS:MembershipPermission.objects.create(membership=member,code=code)
        self.farm.offline_policy_enabled=True;self.farm.save()
        self.api.force_authenticate(user)
        response=self.api.post(PREFIX+'transfers/',self.transfer(),format='json')
        self.assertEqual(response.status_code,403)
        self.assertEqual(response.data['error']['code'],'DEVICE_PROOF_REQUIRED')
        self.assertEqual(Event.objects.count(),1)

    def test_operator_valid_primary_device_signature_is_audited(self):
        import base64,hashlib,json
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from core.models import DeviceRegistration,DeviceChallenge,AuditEvent
        user=User.objects.create_user(username='signed-aquatic-operator',exploitation=self.farm)
        member=ExploitationMembership.objects.get(user=user)
        for code in AQUATIC_PERMISSIONS:MembershipPermission.objects.create(membership=member,code=code)
        self.farm.offline_policy_enabled=True;self.farm.save();key=Ed25519PrivateKey.generate()
        device=DeviceRegistration.objects.create(exploitation=self.farm,installation_uuid=uuid.uuid4(),
            display_name='Synthetic tablet',status='ACTIVE',is_primary_writer=True,write_generation=self.farm.write_generation,
            public_key=key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo).decode())
        challenge=DeviceChallenge.objects.create(device=device,user=user,purpose='WRITE',expires_at=timezone.now()+timezone.timedelta(minutes=5))
        body=json.dumps(self.transfer(),separators=(',',':')).encode();path=PREFIX+'transfers/'
        message=f'ELEVAGE-DEVICE-V1\n{challenge.pk}\n{device.pk}\n{user.pk}\nWRITE\nPOST\n{path}\n{hashlib.sha256(body).hexdigest()}'.encode()
        self.api.force_authenticate(user)
        result=self.api.generic('POST',path,body,content_type='application/json',HTTP_X_ELEVAGE_DEVICE=str(device.pk),
            HTTP_X_ELEVAGE_CHALLENGE=str(challenge.pk),HTTP_X_ELEVAGE_SIGNATURE=base64.b64encode(key.sign(message)).decode())
        self.assertEqual(result.status_code,201,result.data)
        self.assertTrue(AuditEvent.objects.filter(entity_type='pisciculture.event',actor_user_id=user.pk,device_id=device.pk).exists())

    def test_bad_initial_allocation_and_foreign_species_roll_back(self):
        before=Cohort.objects.count()
        payload=dict(id=str(uuid.uuid4()),code='BAD',name='Invalid',species=self.species.pk,
            origin={'type':'OTHER'},started_at=self.at,initial_count=10,
            allocations=[{'installation':str(self.a.pk),'count':9}],reason='Initial')
        self.assertEqual(self.post('cohorts/',**payload).status_code,400)
        other=User.objects.create_user(username='foreign-species-owner')
        foreign=Espece.objects.create(exploitation=other.exploitation,nom='Foreign')
        payload['allocations'][0]['count']=10;payload['species']=foreign.pk
        self.assertEqual(self.post('cohorts/',**payload).status_code,404)
        self.assertEqual(Cohort.objects.count(),before)

    def test_dashboard_at_time_and_scope(self):
        before=(timezone.now()-timezone.timedelta(days=1)).isoformat()
        result=self.api.get(PREFIX+'dashboard/',{'as_of':before}).data
        self.assertEqual(result['metrics']['live_count']['value'],0)
        result=self.api.get(PREFIX+'dashboard/',{'installation':str(self.a.pk)}).data
        self.assertEqual(result['metrics']['live_count']['value'],600)
        for path in ['dashboard/','positions/','events/']:
            key='from' if path=='events/' else 'as_of'
            self.assertEqual(self.api.get(PREFIX+path,{key:'2026-13-45T00:00:00Z'}).status_code,400)

    def test_atomic_rollback_after_first_line(self):
        before=Command.objects.count()
        with patch('pisciculture.services.event',side_effect=RuntimeError('synthetic failure')):
            with self.assertRaises(RuntimeError):self.api.post(PREFIX+'transfers/',self.transfer(),format='json')
        self.assertEqual(sorted(Position.objects.values_list('gross_count',flat=True)),[400,600])
        self.assertEqual(Event.objects.count(),1);self.assertEqual(Command.objects.count(),before)

    def test_antidated_transfer_refused(self):
        body=self.transfer();body['occurred_at']=(timezone.now()-timezone.timedelta(days=5)).isoformat()
        self.assertEqual(self.api.post(PREFIX+'transfers/',body,format='json').status_code,409)

    def test_reserved_hook_blocks_transfer_and_close(self):
        p=Position.objects.get(cohort=self.cohort,installation=self.a);p.reserved_count=10;p.save()
        r=self.api.post(PREFIX+'transfers/',self.transfer(595),format='json')
        self.assertEqual(r.status_code,409);self.assertEqual(r.data['error']['code'],'UNRECONCILED_RESERVE')
        r=self.api.patch(PREFIX+f'cohorts/{self.cohort.pk}/',{'operation_uuid':str(uuid.uuid4()),'schema_version':1,'status':'CLOSED'},format='json',HTTP_IF_MATCH=f'"{self.cohort.pk}:1"')
        self.assertEqual(r.status_code,409)

    def test_capacity_etag_and_no_destructive_delete(self):
        url=PREFIX+f'installations/{self.a.pk}/'
        body={'operation_uuid':str(uuid.uuid4()),'schema_version':1,'name':'Nouveau nom'}
        self.assertEqual(self.api.patch(url,body,format='json').status_code,428)
        self.assertEqual(self.api.patch(url,body,format='json',HTTP_IF_MATCH=f'"{self.a.pk}:1"').status_code,200)
        self.assertEqual(self.api.delete(url).status_code,405)
        self.assertEqual(self.post('installations/',id=str(uuid.uuid4()),code='X',name='X',type='BAC',capacity='10.000').status_code,400)

    def test_pagination_and_foreign_cursor(self):
        first=self.api.get(PREFIX+'positions/',{'limit':1}).data
        second=self.api.get(PREFIX+'positions/',{'limit':1,'cursor':first['next_cursor']}).data
        self.assertNotEqual(first['results'][0]['id'],second['results'][0]['id'])
        self.assertEqual(self.api.get(PREFIX+'positions/',{'limit':1,'installation':str(self.a.pk),'cursor':first['next_cursor']}).status_code,400)

    def test_facts_immutable(self):
        with self.assertRaises(Exception):Event.objects.all().delete()
        if connection.vendor=='postgresql':
            with self.assertRaises(DatabaseError),transaction.atomic():
                with connection.cursor() as c:c.execute('UPDATE pisciculture_event SET reason=reason')

    def test_onboarding_is_atomic_and_replayable(self):
        installation_id=str(uuid.uuid4())
        payload={'operation_uuid':str(uuid.uuid4()),'schema_version':1,
            'installation':{'id':installation_id,'code':'W','name':'Wizard','type':'BAC'},
            'species':{'id':str(uuid.uuid4()),'name':'Aquatic new'},
            'cohort':{'id':str(uuid.uuid4()),'code':'W','name':'Wizard cohort','stage':None,
                'origin':{'type':'OTHER'},'started_at':self.at,'initial_count':10,
                'allocations':[{'installation':installation_id,'count':9}],'reason':'Initial'}}
        before=Installation.objects.count();species_before=Espece.objects.count()
        self.assertEqual(self.api.post(PREFIX+'onboarding/',payload,format='json').status_code,400)
        self.assertEqual(Installation.objects.count(),before);self.assertEqual(Espece.objects.count(),species_before)
        payload['cohort']['allocations'][0]['count']=10
        first=self.api.post(PREFIX+'onboarding/',payload,format='json')
        self.assertEqual(first.status_code,201,first.data)
        self.assertEqual(self.api.post(PREFIX+'onboarding/',payload,format='json').data,first.data)
        self.assertEqual(Installation.objects.count(),before+1)


@skipUnless(connection.vendor=='postgresql','Real concurrency requires PostgreSQL')
class ConcurrencyTests(Fixture,TransactionTestCase):
    def test_two_transfers_cannot_use_same_stock(self):
        barrier=Barrier(2)
        def send(body):
            close_old_connections()
            try:
                user=User.objects.get(pk=self.owner.pk);client=APIClient();client.force_authenticate(user)
                barrier.wait(timeout=10)
                return client.post(PREFIX+'transfers/',body,format='json').status_code
            finally:close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses=list(pool.map(send,[self.transfer(400),self.transfer(400)]))
        self.assertEqual(sorted(statuses),[201,409])
        self.assertEqual(sum(Position.objects.values_list('gross_count',flat=True)),1000)
        self.assertEqual(min(Position.objects.values_list('gross_count',flat=True)),200)

    def test_same_uuid_concurrently_applied_once(self):
        barrier=Barrier(2);body=self.transfer()
        def send(_):
            close_old_connections()
            try:
                client=APIClient();client.force_authenticate(User.objects.get(pk=self.owner.pk));barrier.wait(timeout=10)
                return client.post(PREFIX+'transfers/',body,format='json').data
            finally:close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:responses=list(pool.map(send,range(2)))
        self.assertEqual(responses[0],responses[1]);self.assertEqual(Event.objects.count(),2)
