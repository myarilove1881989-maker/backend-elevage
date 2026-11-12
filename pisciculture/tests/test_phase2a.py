from core.permission_catalog import AQUATIC_PERMISSIONS
import uuid
from decimal import Decimal
from django.test import TestCase
from django.db import connection, transaction, DatabaseError
from django.utils import timezone
from rest_framework.test import APIClient
from core.models import User, MembershipPermission, ExploitationMembership, Lot, Depense
from pisciculture.tests.test_phase1 import Fixture, PREFIX
from pisciculture.models import Observation, ObservationRule, Position, Stage
from pisciculture.services import execute

class Phase2ATests(Fixture,TestCase):
    def weight(self,**extra):
        return dict(cohort=str(self.cohort.pk),occurred_at=timezone.now().isoformat(),
            count_reference_at=timezone.now().isoformat(),sample_count=50,mass_kg='12.5',**extra)
    def w(self,**extra):
        d=self.weight();d['count_reference_at']=self.at;d.update(extra);return self.post('weights/',**d)
    def water(self,**extra):
        return self.post('water-readings/',installation=str(self.a.pk),occurred_at=timezone.now().isoformat(),**extra)
    def rule(self,**extra):
        d=dict(parameter='oxygen_mg_l',scope='INSTALLATION',installation=str(self.a.pk),
            lower='5',valid_from=self.at);d.update(extra);return self.post('observation-rules/',**d)

    def test_sample_and_direct_exact_ratios(self):
        r=self.w();self.assertEqual(r.status_code,201,r.data)
        self.assertEqual(r.data['result']['mean_g']['decimal'],'250')
        self.assertEqual(r.data['result']['biomass_kg']['decimal'],'250')
        d=self.weight();d.update(count_reference_at=self.at,sample_count=3,mass_kg='1')
        r=self.post('weights/',**d);self.assertEqual(r.status_code,201,r.data)
        self.assertEqual(r.data['result']['mean_g']['numerator'],'1000')
        self.assertEqual(r.data['result']['mean_g']['denominator'],'3')
        self.assertEqual(r.data['result']['biomass_kg']['numerator'],'1000')
        d.pop('sample_count');d.pop('mass_kg');d['direct_mean_g']='0.000001'
        r=self.post('weights/',**d);self.assertEqual(r.status_code,201,r.data)
        self.assertEqual(r.data['result']['mean_g']['numerator'],'1')
        self.assertEqual(r.data['result']['mean_g']['denominator'],'1000000')

    def test_installation_does_not_change_cohort_and_history(self):
        first=self.w()
        d=self.weight();d.pop('cohort');d.update(installation=str(self.a.pk),count_reference_at=self.at)
        r=self.post('weights/',**d);self.assertEqual(r.status_code,201,r.data)
        self.assertEqual(r.data['result']['biomass_kg']['decimal'],'150')
        rows=self.api.get(PREFIX+'weights/',{'cohort':str(self.cohort.pk)}).data['results']
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['id'],first.data['id'])
        self.assertEqual(self.cohort.positions.get(installation=self.a).gross_count,600)
        self.assertEqual(Lot.objects.count(),0);self.assertEqual(Depense.objects.count(),0)

    def test_uncertain_count_no_claim_of_exact_biomass(self):
        Position.objects.filter(cohort=self.cohort,installation=self.a).update(reserved_count=10)
        r=self.w();self.assertEqual(r.status_code,201,r.data)
        self.assertIsNone(r.data['result']['biomass_kg'])
        self.assertEqual(r.data['result']['count_reference']['quality'],'UNRECONCILED')
        self.assertEqual(r.data['result']['gross_biomass_estimate_kg']['decimal'],'250')

    def test_reference_at_date_and_provenance(self):
        self.api.post(PREFIX+'transfers/',self.transfer(),format='json')
        d=self.weight();d.pop('cohort');d.update(installation=str(self.a.pk),count_reference_at=self.at)
        r=self.post('weights/',**d)
        self.assertEqual(r.status_code,201,r.data)
        self.assertEqual(r.data['result']['count_reference']['gross_count'],600)
        self.assertTrue(r.data['result']['count_reference']['positions'][0]['source_events'])
        d['count_reference_at']=timezone.now().isoformat();d['occurred_at']=timezone.now().isoformat()
        r=self.post('weights/',**d);self.assertEqual(r.status_code,201,r.data)
        self.assertEqual(r.data['result']['count_reference']['gross_count'],450)

    def test_weight_negatives(self):
        for extra in [dict(sample_count=0),dict(sample_count=True),dict(mass_kg=12.5),
            dict(mass_kg='0'),dict(sample_count=1001),dict(direct_mean_g='10'),
            dict(installation=str(self.a.pk)),dict(count_reference_at='2000-01-01T00:00:00Z'),
            dict(count_reference_at='2099-01-01T00:00:00Z'),dict(occurred_at='2026-01-01')]:
            r=self.w(**extra);self.assertEqual(r.status_code,400,(extra,r.data))
        self.assertEqual(Observation.objects.count(),0)

    def test_partial_water_null_zero_no_rule_no_alert(self):
        r=self.water(temperature_c='0',ph=None)
        self.assertEqual(r.status_code,201,r.data)
        self.assertEqual(Decimal(r.data['temperature_c']),Decimal(0))
        self.assertIsNone(r.data['ph']);self.assertIsNone(r.data['oxygen_mg_l'])
        self.assertFalse(r.data['result']['has_alert'])
        self.assertEqual(r.data['result']['evaluations'][0]['status'],'UNCONFIGURED')
        self.assertEqual(self.water().status_code,400)
        self.assertEqual(self.water(ph=None,temperature_c=None).status_code,400)
        self.assertEqual(self.water(ph=7.0).status_code,400)
        self.assertEqual(self.water(ph='NaN').status_code,400)
        self.assertEqual(self.water(ph='Infinity').status_code,400)
        self.assertEqual(self.water(ph='0').status_code,201)

    def test_rule_overlap_dates_and_bound_null(self):
        self.assertEqual(self.rule().status_code,201)
        self.assertEqual(self.rule(upper='10').status_code,409)
        self.assertEqual(self.rule(parameter='ph',lower='8',upper='7').status_code,400)
        self.assertEqual(self.rule(parameter='ph',lower=None,upper=None).status_code,400)
        self.assertEqual(self.rule(parameter='ph',valid_to=self.at).status_code,400)
        r=self.water(oxygen_mg_l='4');self.assertTrue(r.data['result']['has_alert'])
        e=r.data['result']['evaluations'][0]
        self.assertIsNone(e['rule']['upper']);self.assertEqual(e['status'],'LOW')
        self.assertEqual(e['rule']['version'],1)
        self.assertEqual(self.water(temperature_c='0').data['result']['has_alert'],False)

    def test_resolution_and_multi_population_incompatible(self):
        stage=Stage.objects.create(exploitation=self.farm,species=self.species,code='J',name='Jeune')
        self.cohort.stage=stage;self.cohort.save()
        self.a.system_code='RAS';self.a.save()
        base=dict(parameter='ph',valid_from=self.at,lower='6',upper='8')
        r=self.post('observation-rules/',scope='SYSTEM',system_code='RAS',**base)
        self.assertEqual(r.status_code,201,r.data)
        r=self.post('observation-rules/',scope='SPECIES_STAGE',species=self.species.pk,stage=str(stage.pk),
            **{**base,'lower':'7','upper':'9'})
        self.assertEqual(r.status_code,201,r.data)
        water=self.water(ph='6.5')
        self.assertEqual(water.data['result']['evaluations'][0]['rule']['scope'],'SPECIES_STAGE')
        second=Stage.objects.create(exploitation=self.farm,species=self.species,code='A',name='Adulte')
        r=self.post('cohorts/',id=str(uuid.uuid4()),code='D',name='Deuxième',species=self.species.pk,
            stage=str(second.pk),origin={'type':'HATCH'},started_at=self.at,initial_count=100,
            allocations=[{'installation':str(self.a.pk),'count':100}],reason='Initial')
        self.assertEqual(r.status_code,201,r.data)
        r=self.post('observation-rules/',scope='SPECIES_STAGE',species=self.species.pk,stage=str(second.pk),
            **{**base,'lower':'10','upper':'11'})
        self.assertEqual(r.status_code,201,r.data)
        r=self.water(ph='8')
        self.assertEqual(len(r.data['result']['evaluations']),2)
        self.assertEqual(r.data['result']['incompatible_population_thresholds'],['ph'])
        self.assertTrue(r.data['result']['has_alert'])
        self.assertEqual(self.rule(parameter='ph',lower='5',upper='12').status_code,201)
        r=self.water(ph='8')
        self.assertEqual({e['rule']['scope'] for e in r.data['result']['evaluations']},{'INSTALLATION'})
        self.assertFalse(r.data['result']['has_alert'])

    def test_permissions_tenant_replay_and_disabled(self):
        body=self.weight();body['count_reference_at']=self.at
        body.update(operation_uuid=str(uuid.uuid4()),schema_version=1)
        first=self.api.post(PREFIX+'weights/',body,format='json')
        again=self.api.post(PREFIX+'weights/',body,format='json')
        self.assertEqual(again['Idempotent-Replay'],'true');self.assertEqual(first.data,again.data)
        body['mass_kg']='13';self.assertEqual(self.api.post(PREFIX+'weights/',body,format='json').status_code,409)
        user=User.objects.create_user(username='phase2-operator',exploitation=self.farm)
        member=ExploitationMembership.objects.get(user=user)
        self.api.force_authenticate(user)
        self.assertEqual(self.w().status_code,403);self.assertEqual(self.rule().status_code,403)
        for code in ['aquatic.read','aquatic.weights.create','aquatic.water.create']:
            MembershipPermission.objects.create(membership=member,code=code,created_by=self.owner)
        self.assertEqual(self.w().status_code,201);self.assertEqual(self.water(ph='7').status_code,201)
        self.assertEqual(self.rule().status_code,403)
        MembershipPermission.objects.create(membership=member,code='aquatic.water.rules.manage',created_by=self.owner)
        self.assertEqual(self.rule().status_code,201)
        MembershipPermission.objects.filter(membership=member,code='aquatic.water.create').delete()
        self.assertEqual(self.water(ph='7').status_code,403)
        other=User.objects.create_user(username='phase2-other')
        from pisciculture.models import Activity
        Activity.objects.create(exploitation=other.exploitation,enabled=True)
        self.api.force_authenticate(other)
        self.assertEqual(self.api.get(PREFIX+'weights/').data['results'],[])
        self.assertEqual(self.w().status_code,404)
        self.assertEqual(self.water(ph='7').status_code,404)
        self.assertEqual(self.rule().status_code,404)

    def test_history_pagination_and_policy_snapshot(self):
        self.assertEqual(self.rule(parameter='weight_age_days',lower=None,upper='1').status_code,201)
        self.w();self.w()
        first=self.api.get(PREFIX+'weights/',{'limit':1}).data
        self.assertIsNotNone(first['next_cursor'])
        second=self.api.get(PREFIX+'weights/',{'limit':1,'cursor':first['next_cursor']}).data
        self.assertNotEqual(first['results'][0]['id'],second['results'][0]['id'])
        self.assertEqual(first['results'][0]['freshness']['policy'],None) # Cohort target does not inherit arbitrary basin rule.
        bad=self.api.get(PREFIX+'weights/',{'cohort':str(self.cohort.pk),'cursor':first['next_cursor'],'limit':1})
        self.assertEqual(bad.status_code,400)

    def test_facts_immutable_orm_and_sql(self):
        r=self.w();obj=Observation.objects.get(pk=r.data['id'])
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):obj.save()
        with self.assertRaises(ValidationError):Observation.objects.all().update(note='rewrite')
        if connection.vendor=='postgresql':
            with self.assertRaises(DatabaseError),transaction.atomic():
                with connection.cursor() as c:c.execute('UPDATE pisciculture_observation SET note=%s',['rewrite'])

    def test_water_snapshot_does_not_change_when_rule_added(self):
        before=self.water(ph='7').data['result']
        self.rule(parameter='ph',lower='8')
        row=self.api.get(PREFIX+'water-readings/').data['results'][0]
        self.assertEqual(row['result'],before)

    def test_rule_replacement_is_dated_and_preserves_evaluations(self):
        start=timezone.now()-timezone.timedelta(hours=1)
        switch=timezone.now()-timezone.timedelta(minutes=30)
        old=self.rule(valid_from=start.isoformat(),lower='5')
        self.assertEqual(old.status_code,201,old.data)
        historical=self.post('water-readings/',installation=str(self.a.pk),
            occurred_at=(start+timezone.timedelta(minutes=1)).isoformat(),oxygen_mg_l='4')
        replacement=self.rule(valid_from=switch.isoformat(),lower='3',supersedes=old.data['id'])
        self.assertEqual(replacement.status_code,201,replacement.data)
        self.assertEqual(replacement.data['version'],2)
        current=self.water(oxygen_mg_l='4');self.assertFalse(current.data['result']['has_alert'])
        self.assertEqual(current.data['result']['evaluations'][0]['rule']['id'],replacement.data['id'])
        history=self.api.get(PREFIX+'water-readings/').data['results']
        self.assertEqual(history[0]['result'],historical.data['result'])
        self.assertEqual(self.rule(valid_from=timezone.now().isoformat(),supersedes=old.data['id']).status_code,409)
        future=(timezone.now()+timezone.timedelta(days=10)).isoformat()
        self.assertEqual(self.rule(parameter='ph',valid_to=future).status_code,201)

    def test_metadata_stage_reconstructed_for_backdated_water(self):
        first=Stage.objects.create(exploitation=self.farm,species=self.species,code='J',name='Jeune')
        second=Stage.objects.create(exploitation=self.farm,species=self.species,code='A',name='Adulte')
        from core.audit import ActorContext,audit_scope
        with audit_scope(ActorContext(self.owner.pk,self.farm.pk,owner_http=True)):
            self.cohort.stage=first;self.cohort.save()
        at=timezone.now()
        with audit_scope(ActorContext(self.owner.pk,self.farm.pk,owner_http=True)):
            self.cohort.stage=second;self.cohort.save()
        for stage,lower in [(first,'5'),(second,'3')]:
            rule=self.post('observation-rules/',parameter='oxygen_mg_l',scope='SPECIES_STAGE',
                species=self.species.pk,stage=str(stage.pk),lower=lower,valid_from=self.at)
            self.assertEqual(rule.status_code,201,rule.data)
        r=self.post('water-readings/',installation=str(self.a.pk),occurred_at=at.isoformat(),oxygen_mg_l='4')
        self.assertEqual(r.status_code,201,r.data)
        self.assertEqual(r.data['result']['evaluations'][0]['rule']['stage'],str(first.pk))
        self.assertTrue(r.data['result']['has_alert'])

    def test_weight_rollback_is_atomic_after_fact_created(self):
        from unittest.mock import patch
        from pisciculture.models import Command
        before=Command.objects.count()
        with patch('pisciculture.services.Command.objects.create',side_effect=RuntimeError('synthetic')):
            with self.assertRaises(RuntimeError):self.w()
        self.assertEqual(Observation.objects.count(),0);self.assertEqual(Command.objects.count(),before)

    def test_disabled_and_device_proof_required_new_routes(self):
        from pisciculture.models import Activity
        Activity.objects.filter(exploitation=self.farm).update(enabled=False)
        for route in ['weights/','water-readings/','observation-rules/']:
            self.assertEqual(self.api.get(PREFIX+route).status_code,403)
        Activity.objects.filter(exploitation=self.farm).update(enabled=True)
        user=User.objects.create_user(username='device-phase2',exploitation=self.farm)
        member=ExploitationMembership.objects.get(user=user)
        for code in ['aquatic.weights.create','aquatic.water.create','aquatic.water.rules.manage']:
            MembershipPermission.objects.create(membership=member,code=code)
        self.farm.offline_policy_enabled=True;self.farm.save();self.api.force_authenticate(user)
        self.assertEqual(self.w().data['error']['code'],'DEVICE_PROOF_REQUIRED')
        self.assertEqual(self.water(ph='7').data['error']['code'],'DEVICE_PROOF_REQUIRED')
        self.assertEqual(self.rule().data['error']['code'],'DEVICE_PROOF_REQUIRED')

    def test_new_water_device_signature_is_audited(self):
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
        body=json.dumps({'operation_uuid':str(uuid.uuid4()),'schema_version':1,'installation':str(self.a.pk),'occurred_at':timezone.now().isoformat(),'ph':'7'},separators=(',',':')).encode();path=PREFIX+'water-readings/'
        message=f'ELEVAGE-DEVICE-V1\n{challenge.pk}\n{device.pk}\n{user.pk}\nWRITE\nPOST\n{path}\n{hashlib.sha256(body).hexdigest()}'.encode()
        self.api.force_authenticate(user)
        result=self.api.generic('POST',path,body,content_type='application/json',HTTP_X_ELEVAGE_DEVICE=str(device.pk),
            HTTP_X_ELEVAGE_CHALLENGE=str(challenge.pk),HTTP_X_ELEVAGE_SIGNATURE=base64.b64encode(key.sign(message)).decode())
        self.assertEqual(result.status_code,201,result.data)
        self.assertTrue(AuditEvent.objects.filter(entity_type='pisciculture.observation',actor_user_id=user.pk,device_id=device.pk).exists())


from django.test import TransactionTestCase
from django.db import close_old_connections
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless

@skipUnless(connection.vendor=='postgresql','Real PostgreSQL connections required')
class Phase2AConcurrencyTests(Fixture,TransactionTestCase):
    def test_weight_same_uuid_is_applied_once(self):
        body={'operation_uuid':str(uuid.uuid4()),'schema_version':1,'cohort':str(self.cohort.pk),
            'occurred_at':timezone.now().isoformat(),'count_reference_at':self.at,'direct_mean_g':'250'}
        barrier=Barrier(2)
        def send(_):
            close_old_connections()
            try:
                client=APIClient();client.force_authenticate(User.objects.get(pk=self.owner.pk))
                barrier.wait(timeout=10)
                r=client.post(PREFIX+'weights/',body,format='json')
                return r.status_code,r.data
            finally:close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:responses=list(pool.map(send,range(2)))
        self.assertEqual(responses[0],responses[1]);self.assertEqual(responses[0][0],201)
        self.assertEqual(Observation.objects.count(),1)
