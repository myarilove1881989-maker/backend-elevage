from concurrent.futures import ThreadPoolExecutor
import json
from django.db import connection, close_old_connections
from django.test import TestCase, TransactionTestCase
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken
from core.models import User, Exploitation, ExploitationMembership, MembershipPermission, AuditEvent

INITIAL='Synthetic-Initial-837!'
FINAL='Synthetic-Personal-492!'
RESET='Synthetic-Recovery-573!'


class UserAdministrationTests(TestCase):
    def setUp(self):
        self.owner=User.objects.create_user(username='admin-owner',password=INITIAL)
        self.foreign=User.objects.create_user(username='foreign-owner',password=INITIAL)
        self.api=APIClient();self.api.force_authenticate(self.owner)

    def create(self,name='Jean',rights=None):
        result=self.api.post('/api/memberships/',{'display_name':name,'username':name,
            'email':'','password':INITIAL,'permissions':rights or []},format='json')
        self.assertEqual(result.status_code,201,result.data)
        return result.data

    def login(self,name,password):
        client=APIClient()
        return client.post('/api/token/',{'username':name,'password':password},format='json')

    def complete(self,name):
        pending=self.login(name,INITIAL)
        self.assertEqual(pending.status_code,200,pending.data)
        self.assertNotIn('access',pending.data);self.assertNotIn('refresh',pending.data)
        token=pending.data['change_token']
        client=APIClient()
        result=client.post('/api/password/change-initial/',{'change_token':token,'new_password':FINAL},format='json')
        self.assertEqual(result.status_code,200,result.data)
        return token

    def test_create_jean_paul_atomically_no_farm_owner_or_default_rights(self):
        farms=Exploitation.objects.count()
        jean=self.create('Jean',['purchases.create','mortality.create'])
        paul=self.create('Paul',['animal_sales.create','births.create'])
        blank=self.create('Blank')
        for row in (jean,paul,blank):
            user=User.objects.get(pk=row['user'])
            self.assertEqual(user.exploitation_id,self.owner.exploitation_id)
            self.assertTrue(user.password_change_required)
            self.assertTrue(user.check_password(INITIAL));self.assertNotEqual(user.password,INITIAL)
            self.assertEqual(row['role'],'OPERATEUR');self.assertNotIn('password',row)
        self.assertEqual(blank['permissions'],[])
        self.assertEqual(jean['permissions'],['mortality.create','purchases.create'])
        self.assertEqual(paul['permissions'],['animal_sales.create','births.create'])
        self.assertEqual(Exploitation.objects.count(),farms)
        self.assertEqual(ExploitationMembership.objects.filter(exploitation=self.owner.exploitation,role='OWNER').count(),1)
        invalid=self.api.post('/api/memberships/',{'username':'Bad','password':INITIAL,'permissions':['users.manage']},format='json')
        self.assertEqual(invalid.status_code,400);self.assertFalse(User.objects.filter(username='Bad').exists())

    def test_four_character_operator_password_and_owner_policy(self):
        from core.credential_services import validate_new_password
        from rest_framework.exceptions import ValidationError
        result=self.api.post('/api/memberships/',{'display_name':'Short',
            'username':'short','password':'1234','permissions':[]},format='json')
        self.assertEqual(result.status_code,201,result.data)
        pending=self.login('short','1234')
        self.assertTrue(pending.data['password_change_required'])
        changed=APIClient().post('/api/password/change-initial/',{
            'change_token':pending.data['change_token'],'new_password':'4321'},format='json')
        self.assertEqual(changed.status_code,200,changed.data)
        self.assertIn('access',self.login('short','4321').data)
        self.assertNotEqual(self.login('short','1234').status_code,200)
        member=ExploitationMembership.objects.get(pk=result.data['id'])
        reset=self.api.post(f'/api/memberships/{member.pk}/reset-password/',
            {'new_password':'9876','expected_version':member.version},format='json')
        self.assertEqual(reset.status_code,200,reset.data)
        self.assertTrue(self.login('short','9876').data['password_change_required'])
        with self.assertRaises(ValidationError):
            validate_new_password(self.owner,'1234')

    def test_initial_authorization_never_business_session_and_one_time(self):
        row=self.create(rights=['purchases.create'])
        pending=self.login('Jean',INITIAL)
        self.assertTrue(pending.data['password_change_required'])
        client=APIClient();client.credentials(HTTP_AUTHORIZATION='Bearer '+pending.data['change_token'])
        self.assertEqual(client.get('/api/dashboard/').status_code,401)
        # Even a manually issued regular access cannot bypass the pending state.
        client.credentials(HTTP_AUTHORIZATION='Bearer '+str(RefreshToken.for_user(User.objects.get(pk=row['user'])).access_token))
        self.assertEqual(client.get('/api/dashboard/').status_code,403)
        token=self.complete('Jean')
        again=APIClient().post('/api/password/change-initial/',{'change_token':token,'new_password':RESET},format='json')
        self.assertEqual(again.status_code,403)
        normal=self.login('Jean',FINAL)
        self.assertIn('access',normal.data)
        self.assertNotEqual(self.login('Jean',INITIAL).status_code,200)

    def test_owner_reset_revoke_old_access_refresh_and_no_email_recovery(self):
        row=self.create();self.complete('Jean')
        old=self.login('Jean',FINAL).data
        member=ExploitationMembership.objects.get(pk=row['id'])
        reset=self.api.post(f'/api/memberships/{member.pk}/reset-password/',
            {'new_password':RESET,'expected_version':member.version},format='json')
        self.assertEqual(reset.status_code,200,reset.data)
        self.assertTrue(reset.data['password_change_required']);self.assertNotIn('new_password',reset.data)
        client=APIClient();client.credentials(HTTP_AUTHORIZATION='Bearer '+old['access'])
        self.assertEqual(client.get('/api/dashboard/').status_code,401)
        self.assertNotEqual(APIClient().post('/api/token/refresh/',{'refresh':old['refresh']},format='json').status_code,200)
        self.assertNotEqual(self.login('Jean',FINAL).status_code,200)
        self.assertTrue(self.login('Jean',RESET).data['password_change_required'])
        stale=self.api.post(f'/api/memberships/{member.pk}/reset-password/',
            {'new_password':INITIAL,'expected_version':member.version},format='json')
        self.assertEqual(stale.status_code,400)

    def test_operator_own_and_colleague_admin_denied_foreign_owner_denied(self):
        jean=self.create('Jean');paul=self.create('Paul')
        for row in (jean,paul):
            self.complete(row['username'])
            client=APIClient();client.force_authenticate(User.objects.get(pk=row['user']))
            self.assertEqual(client.get('/api/memberships/').status_code,403)
            self.assertEqual(client.patch(f"/api/memberships/{jean['id']}/",{'permissions':[],'expected_version':1},format='json').status_code,403)
            self.assertEqual(client.post(f"/api/memberships/{jean['id']}/reset-password/",{'new_password':RESET,'expected_version':1},format='json').status_code,403)
        self.api.force_authenticate(self.foreign)
        self.assertEqual(self.api.post(f"/api/memberships/{jean['id']}/reset-password/",{'new_password':RESET,'expected_version':2},format='json').status_code,404)
        self.api.force_authenticate(self.owner)
        self.assertEqual(self.api.post('/api/achats/create/',{},format='json').status_code,403)

    def test_disable_reactivate_old_sessions_stay_revoked(self):
        row=self.create();self.complete('Jean');old=self.login('Jean',FINAL).data
        member=ExploitationMembership.objects.get(pk=row['id'])
        self.api.patch(f'/api/memberships/{member.pk}/',{'is_active':False,'expected_version':member.version},format='json')
        self.assertNotEqual(self.login('Jean',FINAL).status_code,200)
        member.refresh_from_db()
        self.api.patch(f'/api/memberships/{member.pk}/',{'is_active':True,'expected_version':member.version},format='json')
        client=APIClient();client.credentials(HTTP_AUTHORIZATION='Bearer '+old['access'])
        self.assertEqual(client.get('/api/dashboard/').status_code,401)
        self.assertEqual(self.login('Jean',FINAL).status_code,200)

    def test_legacy_sessions_and_accounts_not_retroactively_forced(self):
        user=User.objects.create_user(username='legacy',exploitation=self.owner.exploitation,password=INITIAL)
        self.assertFalse(user.password_change_required)
        self.assertEqual(user.credential_version,1)
        legacy=str(RefreshToken.for_user(user).access_token)
        client=APIClient();client.credentials(HTTP_AUTHORIZATION='Bearer '+legacy)
        self.assertEqual(client.get('/api/dashboard/').status_code,200)
        self.assertIn('access',self.login('legacy',INITIAL).data)

    def test_weak_unknown_fields_and_audit_no_secret(self):
        row=self.create();token=self.complete('Jean')
        events=json.dumps(list(AuditEvent.objects.values('before_data','after_data','reason_text')),default=str)
        for secret in (INITIAL,FINAL,token):self.assertNotIn(secret,events)
        self.assertTrue(AuditEvent.objects.filter(entity_type='core.user',category='SECURITY').exists())
        for password in ('abc','    '):
            result=self.api.post('/api/memberships/',{'username':'weak','password':password},format='json')
            self.assertEqual(result.status_code,400)
        result=self.api.patch(f"/api/memberships/{row['id']}/",{'role':'OWNER'},format='json')
        self.assertEqual(result.status_code,400)

    def test_reset_revokes_grant_but_keeps_original_declaration(self):
        import uuid
        from datetime import timedelta
        from django.utils import timezone
        from core.models import DeviceRegistration, OfflineAuthorization, TerrainSubmission
        row=self.create();self.complete('Jean')
        member=ExploitationMembership.objects.get(pk=row['id'])
        device=DeviceRegistration.objects.create(exploitation=self.owner.exploitation,
            installation_uuid=uuid.uuid4(),display_name='Synthetic',public_key='synthetic-public')
        grant=OfflineAuthorization.objects.create(membership=member,device=device,
            capabilities={'retained':True},rights_version=member.version,write_generation=1,
            expires_at=timezone.now()+timedelta(days=7))
        original=TerrainSubmission.objects.create(exploitation=self.owner.exploitation,device=device,
            offline_authorization=grant,client_operation_id=uuid.uuid4(),local_sequence=1,
            author_user_id=member.user_id,author_membership_id=member.pk,device_generation=1,
            entity_type='ACHAT',operation_type='CREATE',payload={'original':'retained'},
            business_occurred_at=timezone.now(),local_recorded_at=timezone.now(),declaration_digest='a'*64)
        before=(original.client_operation_id,original.payload,original.author_user_id,original.offline_authorization_id)
        result=self.api.post(f'/api/memberships/{member.pk}/reset-password/',
            {'new_password':RESET,'expected_version':member.version},format='json')
        self.assertEqual(result.status_code,200,result.data)
        grant.refresh_from_db();original.refresh_from_db()
        self.assertIsNotNone(grant.revoked_at);self.assertEqual(grant.capabilities,{'retained':True})
        self.assertEqual((original.client_operation_id,original.payload,original.author_user_id,original.offline_authorization_id),before)
        member.refresh_from_db()
        self.api.patch(f'/api/memberships/{member.pk}/',{'is_active':True,'expected_version':member.version},format='json')
        grant.refresh_from_db();self.assertIsNotNone(grant.revoked_at)

    def test_expired_wrong_type_and_same_initial_password_rejected(self):
        from datetime import timedelta
        from django.utils import timezone
        from core.credential_services import InitialPasswordToken
        row=self.create()
        token=InitialPasswordToken.for_user(User.objects.get(pk=row['user']))
        token['credential_version']=1
        token.set_exp(from_time=timezone.now()-timedelta(minutes=10))
        client=APIClient()
        expired=client.post('/api/password/change-initial/',{'change_token':str(token),'new_password':FINAL},format='json')
        self.assertEqual(expired.status_code,403)
        owner_access=str(RefreshToken.for_user(self.owner).access_token)
        self.assertEqual(client.post('/api/password/change-initial/',{'change_token':owner_access,'new_password':FINAL},format='json').status_code,403)
        pending=self.login('Jean',INITIAL).data['change_token']
        self.assertEqual(client.post('/api/password/change-initial/',{'change_token':pending,'new_password':INITIAL},format='json').status_code,400)


class ConcurrentResetTests(TransactionTestCase):
    def test_two_resets_same_version_only_one_wins(self):
        if connection.vendor!='postgresql':self.skipTest('PostgreSQL lock proof')
        owner=User.objects.create_user(username='concurrent-reset-owner')
        user=User.objects.create_user(username='concurrent-reset-user',exploitation=owner.exploitation)
        member=user.memberships.get()
        def reset(password):
            close_old_connections()
            try:
                api=APIClient();api.force_authenticate(owner)
                return api.post(f'/api/memberships/{member.pk}/reset-password/',{'new_password':password,'expected_version':1},format='json').status_code
            finally:close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(reset,[INITIAL,RESET]))
        self.assertEqual(sorted(results),[200,400])
        member.refresh_from_db();user.refresh_from_db()
        self.assertEqual(member.version,2);self.assertEqual(user.credential_version,2)
