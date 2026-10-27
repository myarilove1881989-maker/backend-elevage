import copy
import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch
import jwt
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.test import APIClient
from core.models import (MembershipPermission, OfflineAuthorization, TerrainSubmission,
    TerrainDecision, AuditEvent, Client, User)
from core.tests.test_terrain_transport import TransportFixture
from core.offline_permissions import contract_for, fine_contract, permission_failure
from core.access_policy import effective_permissions, authorize_business_service
from core.permission_catalog import ENTITY_DOMAINS, operation_action
from core.terrain_application import process_pending, context_for, HANDLERS
from core.audit import audit_scope
from core.foundation_views import issue_grant


class OfflinePermissionTests(TransportFixture, TestCase):
    def setUp(self):
        super().setUp()
        MembershipPermission.objects.filter(membership=self.member).delete()
        self.right('clients.create')
        self.grant.expires_at = self.grant.issued_at + timedelta(hours=24)
        self.grant.save(update_fields=['expires_at'])
        self.make_fine()

    def right(self, code, user=None):
        member = (user or self.jean).memberships.get(exploitation=self.farm)
        MembershipPermission.objects.get_or_create(membership=member, code=code,
            defaults={'created_by': self.owner})

    def make_fine(self):
        # Only synthetic fixture creation; never upgrades real or historical grants.
        self.grant.capabilities['permission_contract'] = contract_for(
            self.grant, effective_permissions(self.member))
        self.grant.save(update_fields=['capabilities'])

    def original(self, sequence=1):
        data=self.operation(sequence)
        data['entity_type']='CLIENT'
        return data

    def parsed(self, data):
        from core.terrain_transport import SubmissionInput
        return SubmissionInput().run_validation(data)

    def test_exact_action_matrix_and_no_general_capability_bypass(self):
        for index,(kind,domain) in enumerate(ENTITY_DOMAINS.items()):
            action=domain+'.create'
            data=self.parsed(self.original(index+1));data['entity_type']=kind
            if action not in fine_contract(self.grant):
                self.assertEqual(permission_failure(self.grant,data),'OFFLINE_PERMISSION_DENIED')
            self.right(action);self.make_fine()
            self.assertEqual(permission_failure(self.grant,data),'')
            self.assertEqual(permission_failure(self.grant,data,action='users.manage'),'OFFLINE_ACTION_UNSUPPORTED')
        data=self.parsed(self.original());data.update(entity_type='TASK',operation_type='UPDATE')
        self.assertEqual(permission_failure(self.grant,data),'OFFLINE_PERMISSION_DENIED')
        self.right('agenda.progress');self.make_fine()
        self.assertEqual(permission_failure(self.grant,data),'')
        for operation in ('CREATE','CANCEL','REVERSE'):
            data['operation_type']=operation
            self.assertEqual(permission_failure(self.grant,data),'OFFLINE_ACTION_UNSUPPORTED')

    def test_unknown_malformed_binding_duplicate_and_owner_contracts_fail_closed(self):
        good=copy.deepcopy(self.grant.capabilities['permission_contract'])
        variants=[None,{},dict(good,version=99),dict(good,grant_version=True),
            dict(good,user=self.paul.pk),dict(good,exploitation=self.farm.pk+1),
            dict(good,device_id=self.device.pk+1),dict(good,write_generation=99),
            dict(good,rights_version=99),dict(good,grant_id=str(uuid.uuid4())),
            dict(good,membership_id=self.member.pk+1),dict(good,iat=good['iat']+1),
            dict(good,exp=good['exp']+1),dict(good,role='OWNER'),
            dict(good,permissions=['finance.read','finance.read']),
            dict(good,permissions=['finance.read','users.manage']),
            dict(good,permissions=['clients.create']),dict(good,permissions=['finance.read','invented.write'])]
        for value in variants:
            with self.subTest(value=value):
                self.grant.capabilities['permission_contract']=value
                self.assertEqual(permission_failure(self.grant,self.parsed(self.original())),
                    'OFFLINE_CONTRACT_INVALID')

    def test_personal_farm_device_and_generation_contexts(self):
        data=self.parsed(self.original())
        for key in ('author_user_id','author_membership_id','exploitation_id','device_id','device_generation'):
            bad=dict(data);bad[key]+=10000
            self.assertEqual(permission_failure(self.grant,bad),'OFFLINE_CONTEXT_INVALID')
        bad=dict(data,offline_authorization_id=uuid.uuid4())
        self.assertEqual(permission_failure(self.grant,bad),'OFFLINE_CONTEXT_INVALID')
        self.right('animal_sales.create',self.paul)
        self.assertNotIn('animal_sales.create',fine_contract(self.grant))
        foreign=User.objects.create_user(username='fine-foreign')
        bad=self.original();bad['exploitation_id']=foreign.exploitation_id
        response,_=self.send(bad)
        self.assertEqual(response.status_code,403)
        self.assertFalse(TerrainSubmission.objects.exists())

    def test_outside_window_and_boundary_are_preserved_without_effect(self):
        for index,time in enumerate((self.grant.issued_at-timedelta(seconds=2),
                self.grant.expires_at,self.grant.expires_at+timedelta(days=1))):
            data=self.original(index+1);data['local_recorded_at']=time.isoformat()
            response,_=self.send(data)
            self.assertEqual(response.status_code,200,response.data)
            self.assertEqual(response.data['receipts'][0]['business_status'],'NEEDS_RECONCILIATION')
        self.assertEqual(TerrainSubmission.objects.count(),3)
        self.assertFalse(Client.objects.exists())

    def test_delayed_valid_original_after_expiry_still_applies_once(self):
        self.grant.issued_at=timezone.now()-timedelta(days=2)
        self.grant.expires_at=timezone.now()-timedelta(days=1)
        self.grant.save();self.make_fine()
        data=self.original();data['local_recorded_at']=(self.grant.issued_at+timedelta(hours=1)).isoformat()
        response,_=self.send(data);repeat,_=self.send(data)
        self.assertEqual(response.data['receipts'][0]['business_status'],'CONFIRMED')
        self.assertEqual(repeat.data,response.data)
        self.assertEqual(Client.objects.count(),1)
        self.assertEqual(AuditEvent.objects.filter(action='TERRAIN_APPLIED').count(),1)

    def test_revocation_offline_retains_original_uuid_author_grant_and_payload(self):
        data=self.original();self.grant.revoked_at=timezone.now();self.grant.save()
        response,_=self.send(data)
        self.assertEqual(response.status_code,200,response.data)
        self.assertEqual(response.data['receipts'][0]['business_status'],'NEEDS_RECONCILIATION')
        row=TerrainSubmission.objects.get()
        self.assertEqual(str(row.client_operation_id),data['client_operation_id'])
        self.assertEqual(row.author_user_id,self.jean.pk)
        self.assertEqual(row.offline_authorization_id,self.grant.pk)
        self.assertEqual(row.payload,data['payload'])
        self.assertFalse(Client.objects.exists())

    def test_receive_then_permission_change_before_application(self):
        data=self.original()
        with patch('core.terrain_views.process_pending'):
            response,_=self.send(data)
        self.assertEqual(response.data['receipts'][0]['business_status'],'UNREVIEWED')
        self.member.version+=1;self.member.save()
        process_pending(self.device.pk)
        row=TerrainSubmission.objects.get()
        self.assertEqual(row.outcome.business_status,'NEEDS_RECONCILIATION')
        self.assertFalse(Client.objects.exists())
        repeat,_=self.send(data)
        self.assertEqual(repeat.status_code,200)
        self.assertEqual(TerrainSubmission.objects.count(),1)

    def test_apply_rechecks_even_when_permission_row_changed_without_version_increment(self):
        with patch('core.terrain_views.process_pending'):
            self.send(self.original())
        MembershipPermission.objects.filter(membership=self.member,code='clients.create').delete()
        process_pending(self.device.pk)
        self.assertEqual(TerrainSubmission.objects.get().outcome.reason_code,'OFFLINE_PERMISSION_DENIED')
        self.assertFalse(Client.objects.exists())

    def test_stored_invalid_contract_received_durably_not_downgraded_to_v1(self):
        self.grant.capabilities['permission_contract']=None;self.grant.save()
        response,_=self.send(self.original())
        self.assertEqual(response.status_code,200,response.data)
        self.assertEqual(TerrainSubmission.objects.get().outcome.reason_code,'OFFLINE_CONTRACT_INVALID')
        self.assertFalse(Client.objects.exists())

    def test_owner_and_inactive_original_author_cannot_create(self):
        self.member.is_active=False;self.member.save()
        response,_=self.send(self.original())
        self.assertEqual(response.data['receipts'][0]['business_status'],'NEEDS_RECONCILIATION')
        self.assertFalse(Client.objects.exists())
        with self.assertRaises(PermissionDenied):
            authorize_business_service(self.owner,self.farm,'clients.create')

    def test_guarded_service_cannot_bypass_fine_permission_inside_trusted_scope(self):
        with patch('core.terrain_views.process_pending'):
            self.send(self.original())
        row=TerrainSubmission.objects.get()
        with audit_scope(context_for(row)):
            with self.assertRaises(PermissionDenied):
                authorize_business_service(self.jean,self.farm,'purchases.create')

    def test_refusal_is_traceable_and_reapproval_remains_blocked(self):
        self.grant.revoked_at=timezone.now();self.grant.save()
        data=self.original();self.send(data)
        client=APIClient();client.force_authenticate(self.owner)
        path='/api/offline/reconciliation/'+data['client_operation_id']+'/'
        decision={'decision_uuid':str(uuid.uuid4()),'expected_decision_version':0,
            'action':'APPLY_ORIGINAL','reason':'Synthetic security review.'}
        self.assertEqual(client.post(path,decision,format='json').status_code,403)
        decision['action']='CANCEL'
        response=client.post(path,decision,format='json')
        self.assertEqual(response.status_code,403,response.data)
        self.assertEqual(str(response.data['detail']),'V2_RECONCILIATION_READ_ONLY')
        self.assertEqual(client.post(path,decision,format='json').status_code,403)
        self.assertEqual(TerrainDecision.objects.count(),0)
        self.assertFalse(AuditEvent.objects.filter(action='CANCEL',decision_actor_id=self.owner.pk).exists())
        self.assertEqual(TerrainSubmission.objects.get().payload,data['payload'])

    def test_legacy_v1_without_current_right_is_received_without_effect_or_rewriting(self):
        self.grant.capabilities.pop('permission_contract');self.grant.save()
        MembershipPermission.objects.filter(membership=self.member).delete()
        response,_=self.send(self.original())
        self.assertEqual(response.data['receipts'][0]['business_status'],'NEEDS_RECONCILIATION')
        self.assertFalse(Client.objects.exists())
        self.assertEqual(TerrainSubmission.objects.get().outcome.reason_code,'OFFLINE_PERMISSION_DENIED')
        self.assertIsNone(fine_contract(self.grant))
        self.grant.refresh_from_db();self.assertNotIn('permission_contract',self.grant.capabilities)

    def test_legacy_v1_authorized_original_applies_once_without_contract_rewrite(self):
        self.grant.capabilities.pop('permission_contract');self.grant.save()
        data=self.original()
        first,_=self.send(data);repeat,_=self.send(data)
        self.assertEqual(first.data['receipts'][0]['business_status'],'CONFIRMED')
        self.assertEqual(first.data,repeat.data)
        self.assertEqual(Client.objects.count(),1)
        self.grant.refresh_from_db()
        self.assertNotIn('permission_contract',self.grant.capabilities)

    def test_legacy_v1_permission_removed_after_receipt_blocks_application(self):
        self.grant.capabilities.pop('permission_contract');self.grant.save()
        with patch('core.terrain_views.process_pending'):self.send(self.original())
        MembershipPermission.objects.filter(membership=self.member,code='clients.create').delete()
        process_pending(self.device.pk)
        self.assertFalse(Client.objects.exists())
        self.assertEqual(TerrainSubmission.objects.get().outcome.reason_code,'OFFLINE_PERMISSION_DENIED')

    def test_legacy_v1_window_matches_signed_second_and_excludes_expiry(self):
        self.grant.capabilities.pop('permission_contract');self.grant.save()
        for index,instant in enumerate((int(self.grant.issued_at.timestamp()),int(self.grant.expires_at.timestamp())),1):
            data=self.original(index)
            data['local_recorded_at']=timezone.datetime.fromtimestamp(instant,tz=timezone.get_default_timezone()).isoformat()
            response,_=self.send(data)
            row=TerrainSubmission.objects.get(client_operation_id=data['client_operation_id'])
            self.assertEqual(row.outcome.business_status,'CONFIRMED' if index==1 else 'NEEDS_RECONCILIATION')
        self.assertEqual(Client.objects.count(),1)

    def test_signed_second_boundary_is_consistent_at_receipt_and_application(self):
        data=self.original()
        data['local_recorded_at']=timezone.datetime.fromtimestamp(
            int(self.grant.issued_at.timestamp()),tz=timezone.get_default_timezone()).isoformat()
        response,_=self.send(data)
        self.assertEqual(response.status_code,200,response.data)
        self.assertEqual(response.data['receipts'][0]['business_status'],'CONFIRMED')

    def test_false_general_capability_never_creates_effect_in_fine_mode(self):
        self.grant.capabilities['can_create_terrain_operation']=False;self.grant.save()
        response,_=self.send(self.original())
        self.assertEqual(response.status_code,200,response.data)
        self.assertEqual(TerrainSubmission.objects.get().outcome.reason_code,'OFFLINE_PERMISSION_DENIED')
        self.assertFalse(Client.objects.exists())

    def test_future_clock_is_received_but_never_applied(self):
        data=self.original()
        data['local_recorded_at']=(timezone.now()+timedelta(hours=1)).isoformat()
        response,_=self.send(data)
        self.assertEqual(response.status_code,200,response.data)
        self.assertEqual(TerrainSubmission.objects.get().outcome.reason_code,'OFFLINE_CLOCK_INVALID')
        self.assertFalse(Client.objects.exists())

    def test_signature_tampering_still_fails_before_receipt(self):
        response,_=self.send(self.original(),tamper=True)
        self.assertEqual(response.status_code,403)
        self.assertFalse(TerrainSubmission.objects.exists())

    def issue(self, version=None):
        request=SimpleNamespace(user=self.jean,verified_device=self.device,
            data={} if version is None else {'permission_contract_version':version},headers={})
        return issue_grant(request,self.member,self.farm,self.device,Ed25519PrivateKey.generate())

    def test_fine_issuance_disabled_by_default_and_unknown_versions_rejected(self):
        with override_settings(OFFLINE_FINE_PERMISSIONS_ENABLED=False):
            with self.assertRaises(PermissionDenied):self.issue(2)
        with override_settings(OFFLINE_FINE_PERMISSIONS_ENABLED='true'):
            with self.assertRaises(PermissionDenied):self.issue(2)
        for version in (1,3,True,'2'):
            with self.assertRaises(ValidationError):self.issue(version)
        self.assertEqual(OfflineAuthorization.objects.count(),1)

    @override_settings(OFFLINE_FINE_PERMISSIONS_ENABLED=True,OFFLINE_FINE_AUTHORIZATION_HOURS=24)
    def test_opt_in_issuance_binds_contract_to_same_ed25519_signature(self):
        key=Ed25519PrivateKey.generate()
        request=SimpleNamespace(user=self.jean,verified_device=self.device,
            data={'permission_contract_version':2},headers={})
        response=issue_grant(request,self.member,self.farm,self.device,key)
        payload=jwt.decode(response.data['authorization'],key.public_key(),algorithms=['EdDSA'],
            audience='elevage-device',issuer='elevage-offline')
        grant=OfflineAuthorization.objects.get(pk=payload['jti'])
        self.assertEqual(payload['capabilities'],grant.capabilities)
        self.assertIn('clients.create',fine_contract(grant))
        self.assertEqual((grant.expires_at-grant.issued_at).total_seconds(),86400)
        with self.assertRaises(jwt.InvalidSignatureError):
            jwt.decode(response.data['authorization'],Ed25519PrivateKey.generate().public_key(),
                algorithms=['EdDSA'],audience='elevage-device')
        self.grant.refresh_from_db()
        self.assertNotEqual(grant.pk,self.grant.pk)

    @override_settings(OFFLINE_FINE_PERMISSIONS_ENABLED=True)
    def test_old_client_still_gets_v1_only_and_original_grant_unchanged(self):
        self.grant.capabilities.pop('permission_contract');self.grant.save()
        before=copy.deepcopy(self.grant.capabilities)
        result=self.issue()
        payload=jwt.decode(result.data['authorization'],options={'verify_signature':False})
        self.assertNotIn('permission_contract',payload['capabilities'])
        self.grant.refresh_from_db();self.assertEqual(self.grant.capabilities,before)

    def test_fine_history_prevents_new_v1_grants_even_if_fine_issuance_is_disabled(self):
        before=copy.deepcopy(self.grant.capabilities)
        with override_settings(OFFLINE_FINE_PERMISSIONS_ENABLED=False):
            with self.assertRaises(PermissionDenied):self.issue()
        self.assertEqual(OfflineAuthorization.objects.count(),1)
        self.grant.refresh_from_db();self.assertEqual(self.grant.capabilities,before)

    @override_settings(OFFLINE_FINE_PERMISSIONS_ENABLED=True,OFFLINE_FINE_AUTHORIZATION_HOURS=169)
    def test_unsafe_issuance_window_is_blocked_without_new_grant(self):
        with self.assertRaises(ValidationError):self.issue(2)
        self.assertEqual(OfflineAuthorization.objects.count(),1)


    def test_reader_rejects_signed_duration_over_7_days_without_v1_fallback(self):
        self.grant.expires_at=self.grant.issued_at+timedelta(days=7,seconds=1)
        self.grant.save();self.make_fine()
        response,_=self.send(self.original())
        self.assertEqual(response.status_code,200,response.data)
        row=TerrainSubmission.objects.get()
        self.assertEqual(row.outcome.reason_code,'OFFLINE_CONTRACT_INVALID')
        self.assertFalse(Client.objects.exists())

    def test_conversion_survives_generation_change_and_revoked_expired_grant(self):
        self.grant.revoked_at=timezone.now()
        self.grant.issued_at=timezone.now()-timedelta(days=3)
        self.grant.expires_at=self.grant.issued_at+timedelta(hours=24)
        self.grant.save();self.make_fine()
        self.farm.write_generation+=1;self.farm.save()
        self.device.write_generation=self.farm.write_generation;self.device.save()
        with override_settings(OFFLINE_FINE_PERMISSIONS_ENABLED=False):
            with self.assertRaises(PermissionDenied) as error:self.issue()
        self.assertEqual(str(error.exception),'OFFLINE_FINE_CLIENT_REQUIRED')
        self.assertEqual(OfflineAuthorization.objects.count(),1)

    def test_conversion_survives_reinstallation_on_a_different_device(self):
        from core.models import DeviceRegistration
        self.device=DeviceRegistration.objects.create(exploitation=self.farm,
            installation_uuid=uuid.uuid4(),display_name='Synthetic reinstall',
            status='ACTIVE',write_generation=2,public_key=self.device.public_key)
        self.farm.write_generation=2;self.farm.save()
        with self.assertRaises(PermissionDenied):self.issue()
        self.assertEqual(OfflineAuthorization.objects.count(),1)

    @override_settings(OFFLINE_FINE_PERMISSIONS_ENABLED=True)
    def test_pending_v1_original_survives_conversion_and_fine_renewal(self):
        self.grant.capabilities.pop('permission_contract');self.grant.save()
        original=self.original();old_id=self.grant.pk
        with patch('core.terrain_views.process_pending'):self.send(original)
        for _ in range(2):self.issue(2)
        with self.assertRaises(PermissionDenied):self.issue()
        process_pending(self.device.pk)
        first=TerrainSubmission.objects.get()
        self.assertEqual(first.offline_authorization_id,old_id)
        self.assertEqual(first.payload,original['payload'])
        self.assertEqual(first.author_user_id,self.jean.pk)
        self.assertEqual(first.outcome.business_status,'CONFIRMED')
        self.assertNotIn('permission_contract',first.offline_authorization.capabilities)
        repeat,_=self.send(original)
        self.assertEqual(repeat.status_code,200)
        self.assertEqual(Client.objects.count(),1)
        self.assertEqual(OfflineAuthorization.objects.count(),3)

    def test_five_minute_clock_boundary_is_exact_and_preserves_rejected_original(self):
        fixed=timezone.now()
        for index,seconds in enumerate((300,301),1):
            data=self.original(index)
            data['local_recorded_at']=(fixed+timedelta(seconds=seconds)).isoformat()
            with patch('django.utils.timezone.now',return_value=fixed):response,_=self.send(data)
            self.assertEqual(response.status_code,200,response.data)
            row=TerrainSubmission.objects.get(client_operation_id=data['client_operation_id'])
            self.assertEqual(row.outcome.business_status,'CONFIRMED' if seconds==300 else 'NEEDS_RECONCILIATION')
            if seconds==301:self.assertEqual(row.outcome.reason_code,'OFFLINE_CLOCK_INVALID')
            self.assertEqual(row.local_recorded_at.isoformat(),data['local_recorded_at'])
        self.assertEqual(Client.objects.count(),1)

    def test_dst_repeated_wall_time_uses_absolute_instants_not_local_labels(self):
        from datetime import datetime, timezone as utc
        from zoneinfo import ZoneInfo
        paris=ZoneInfo('Europe/Paris')
        first=datetime(2026,10,25,2,30,tzinfo=paris,fold=0)
        second=datetime(2026,10,25,2,30,tzinfo=paris,fold=1)
        self.assertEqual(second.timestamp()-first.timestamp(),3600)
        self.grant.issued_at=first.astimezone(utc.utc)-timedelta(minutes=30)
        self.grant.expires_at=self.grant.issued_at+timedelta(hours=24)
        self.grant.save();self.make_fine()
        for index,instant in enumerate((first,second),1):
            data=self.parsed(self.original(index));data['local_recorded_at']=instant
            with patch('django.utils.timezone.now',return_value=second+timedelta(minutes=10)):
                self.assertEqual(permission_failure(self.grant,data),'')
        rollback=self.parsed(self.original());rollback['local_recorded_at']=first-timedelta(hours=1)
        with patch('django.utils.timezone.now',return_value=second+timedelta(minutes=10)):
            self.assertEqual(permission_failure(self.grant,rollback),'OFFLINE_WINDOW_INVALID')

    def test_clock_backdating_inside_window_cannot_be_proven_by_server(self):
        # Explicit residual limitation: no authenticated local wall clock in V1 payload.
        data=self.parsed(self.original())
        data['local_recorded_at']=self.grant.issued_at+timedelta(seconds=1)
        with patch('django.utils.timezone.now',return_value=self.grant.expires_at+timedelta(days=1)):
            self.assertEqual(permission_failure(self.grant,data),'')

    def test_correction_cannot_reactivate_invalid_fine_author_or_mutate_original(self):
        self.member.is_active=False;self.member.save()
        original=self.original();self.send(original)
        row=TerrainSubmission.objects.get()
        before=(row.author_user_id,row.offline_authorization_id,copy.deepcopy(row.payload),row.declaration_digest)
        client=APIClient();client.force_authenticate(self.owner)
        path='/api/offline/reconciliation/'+original['client_operation_id']+'/'
        for action in ('APPLY_ORIGINAL','CORRECTION'):
            data={'decision_uuid':str(uuid.uuid4()),'expected_decision_version':0,
                'action':action,'reason':'Synthetic blocked correction.',
                'payload':{'nom':'Changed'} if action=='CORRECTION' else {}}
            response=client.post(path,data,format='json')
            self.assertEqual(response.status_code,403,response.data)
            self.assertIn('V2_RECONCILIATION_READ_ONLY',str(response.data))
        row.refresh_from_db()
        self.assertEqual((row.author_user_id,row.offline_authorization_id,row.payload,row.declaration_digest),before)
        self.assertEqual(TerrainDecision.objects.count(),0)
        self.assertFalse(Client.objects.exists())


# Exercise the real concurrent PostgreSQL transport/application path with fine grants.
import core.tests.test_terrain_application as legacy_concurrency

class FineGrantConcurrentApplicationTests(legacy_concurrency.TerrainConcurrentApplicationTests):
    def setUp(self):
        super().setUp()
        MembershipPermission.objects.get_or_create(membership=self.member,code='clients.create',defaults={'created_by':self.owner})
        self.grant.expires_at=self.grant.issued_at+timedelta(hours=24)
        self.grant.expires_at=self.grant.issued_at+timedelta(hours=24)
        self.grant.capabilities['permission_contract']=contract_for(self.grant,effective_permissions(self.member))
        self.grant.save(update_fields=['capabilities','expires_at'])
