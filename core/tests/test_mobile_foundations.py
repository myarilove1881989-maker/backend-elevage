import base64
import hashlib
import json
import uuid
from datetime import timedelta
from unittest.mock import patch
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from django.contrib import admin
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction, connection
from django.test import TestCase, RequestFactory, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework.exceptions import PermissionDenied
from core.models import (User, Exploitation, ExploitationMembership, DeviceRegistration,
                         DeviceChallenge, OfflineAuthorization, AuditEvent, Task, Client,
                         Lot, Espece, AffectationMouvementOeufs)
from core.audit import ActorContext, audit_scope, safe_data
from core.offline_context import validated_offline_context


class MobileFoundationsTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username='owner', password='Strong-test-2091!')
        self.farm = self.owner.exploitation
        self.jean = User.objects.create_user(username='jean', exploitation=self.farm)
        self.paul = User.objects.create_user(username='paul', exploitation=self.farm)
        self.other = User.objects.create_user(username='other')
        self.client = APIClient()
        self.client.force_authenticate(self.owner)
        self.key = Ed25519PrivateKey.generate()
        self.device = self.new_device(self.key)

    def new_device(self, key):
        return DeviceRegistration.objects.create(exploitation=self.farm, installation_uuid=uuid.uuid4(),
            display_name='Tablette', public_key=key.public_key().public_bytes(
                serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode())

    def signed(self, path, data=None, purpose='ACTIVATE', user=None, device=None, key=None,
               method='POST', challenge=None, tampered_body=None):
        user, device, key = user or self.owner, device or self.device, key or self.key
        self.client.force_authenticate(user)
        obj = challenge or DeviceChallenge.objects.create(device=device, user=user, purpose=purpose,
            expires_at=timezone.now() + timedelta(minutes=5))
        body = json.dumps(data or {}, separators=(',', ':')).encode()
        message = (f'ELEVAGE-DEVICE-V1\n{obj.pk}\n{device.pk}\n{user.pk}\n{purpose}\n'
                   f'{method}\n{path}\n{hashlib.sha256(body).hexdigest()}').encode()
        signature = base64.b64encode(key.sign(message)).decode()
        return self.client.generic(method, path, tampered_body or body, content_type='application/json',
            HTTP_X_ELEVAGE_DEVICE=str(device.pk), HTTP_X_ELEVAGE_CHALLENGE=str(obj.pk),
            HTTP_X_ELEVAGE_SIGNATURE=signature)

    def enable(self):
        response = self.signed(f'/api/devices/{self.device.pk}/activate/')
        self.assertEqual(response.status_code, 200, response.data)
        response = self.client.post('/api/offline-policy/enable/')
        self.assertEqual(response.status_code, 200, response.data)
        self.farm.refresh_from_db()
        self.owner.refresh_from_db()
        self.jean.refresh_from_db()

    def task(self, assigned=None):
        return Task.objects.create(exploitation=self.farm, title='Contrôle', date='2026-10-06',
                                   assigned_to=assigned)

    def test_existing_owner_membership_and_default_compatibility(self):
        member = self.owner.memberships.get(exploitation=self.farm)
        self.assertEqual(member.role, 'OWNER')
        self.assertFalse(self.farm.offline_policy_enabled)
        response = self.client.get('/api/me/capabilities/')
        self.assertTrue(response.data['capabilities']['can_create_sale'])
        self.assertFalse(self.owner.is_staff)

    def test_operator_creation_has_no_parasite_farm(self):
        count = Exploitation.objects.count()
        response = self.client.post('/api/memberships/', {'username': 'newoperator',
            'email': 'new@example.test', 'password': 'Unique-safe-password-482!'}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(Exploitation.objects.count(), count)
        self.assertEqual(User.objects.get(username='newoperator').exploitation_id, self.farm.pk)
        event = AuditEvent.objects.get(entity_type='core.exploitationmembership', entity_id=str(response.data['id']))
        self.assertEqual(event.actor_user_id, self.owner.pk)
        self.assertNotIn('password', json.dumps(event.after_data))

    def test_operator_cannot_manage_members_or_devices(self):
        self.client.force_authenticate(self.jean)
        for path in ('/api/memberships/', '/api/devices/', '/api/audit-events/'):
            self.assertEqual(self.client.get(path).status_code, 403)

    def test_membership_cross_tenant_not_found(self):
        member = self.other.memberships.first()
        self.assertEqual(self.client.patch(f'/api/memberships/{member.pk}/',
                         {'is_active': False}, format='json').status_code, 404)

    def test_disabled_operator_denied_even_in_legacy_mode(self):
        member = self.jean.memberships.get(exploitation=self.farm)
        self.assertEqual(self.client.patch(f'/api/memberships/{member.pk}/',
                         {'is_active': False}, format='json').status_code, 200)
        member.refresh_from_db()
        self.assertEqual(member.version, 2)
        self.client.force_authenticate(self.jean)
        self.assertEqual(self.client.get('/api/dashboard/').status_code, 403)

    def test_owner_terrain_denied_but_agenda_allowed(self):
        self.enable()
        for path in ('/api/achats/create/', '/api/depenses/create/', '/api/mouvements/create/',
                     '/api/payments/create/', '/api/oeufs/collectes/', '/api/oeufs/ventes/',
                     '/api/alimentation/distributions/', '/api/production/pesees/', '/api/clients/create/'):
            self.assertEqual(self.client.post(path, {}, format='json').status_code, 403, path)
        response = self.client.post('/api/tasks/', {'title': 'Vaccination', 'date': '2026-10-07',
            'assigned_to': self.jean.pk, 'priority': 'HIGH'}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['created_by'], self.owner.pk)
        self.assertFalse(self.client.get('/api/me/capabilities/').data['capabilities']['can_create_sale'])

    def test_task_assignment_cannot_cross_tenant_or_assign_owner(self):
        for user in (self.owner, self.other):
            response = self.client.post('/api/tasks/', {'title': 'Test', 'date': '2026-10-07',
                'assigned_to': user.pk}, format='json')
            self.assertEqual(response.status_code, 400)

    def test_operator_task_progress_reports_and_audit(self):
        self.enable()
        task = self.task(self.jean)
        self.client.force_authenticate(self.jean)
        path = f'/api/tasks/{task.pk}/'
        self.assertEqual(self.client.patch(path, {'status': 'DONE'}, format='json').status_code, 400)
        self.assertEqual(self.client.patch(path, {'status': 'IN_PROGRESS'}, format='json').status_code, 200)
        result = self.client.patch(path, {'status': 'DONE', 'report': 'Réalisé'}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data['completed_by'], self.jean.pk)
        event = AuditEvent.objects.filter(entity_type='core.task', entity_id=str(task.pk)).first()
        self.assertEqual(event.before_data['status'], 'IN_PROGRESS')
        self.assertEqual(event.after_data['status'], 'DONE')
        self.assertEqual(event.actor_user_id, self.jean.pk)

    def test_paul_cannot_see_or_edit_jeans_task(self):
        self.enable()
        task = self.task(self.jean)
        self.client.force_authenticate(self.paul)
        self.assertEqual(self.client.get(f'/api/tasks/{task.pk}/').status_code, 404)
        self.assertEqual(self.client.patch(f'/api/tasks/{task.pk}/', {'status': 'IN_PROGRESS'},
                         format='json').status_code, 404)

    def test_general_task_accessible_to_operators(self):
        self.enable()
        task = self.task()
        self.client.force_authenticate(self.paul)
        self.assertEqual(self.client.patch(f'/api/tasks/{task.pk}/', {'status': 'IN_PROGRESS'},
                         format='json').status_code, 200)

    def test_operator_cannot_plan_reassign_or_cancel(self):
        self.enable()
        task = self.task(self.jean)
        self.client.force_authenticate(self.jean)
        for data in ({'title': 'Changed'}, {'assigned_to': self.paul.pk}, {'status': 'CANCELLED'}):
            self.assertIn(self.client.patch(f'/api/tasks/{task.pk}/', data, format='json').status_code,
                          (400, 403))
        self.assertEqual(self.client.post('/api/tasks/', {}, format='json').status_code, 403)

    def test_owner_can_cancel_and_task_version_conflict(self):
        self.enable()
        task = self.task(self.jean)
        path = f'/api/tasks/{task.pk}/'
        self.assertEqual(self.client.patch(path, {'status': 'CANCELLED', 'expected_version': 1},
                         format='json').status_code, 200)
        self.assertEqual(self.client.patch(path, {'title': 'Changed', 'expected_version': 1},
                         format='json').status_code, 400)
        self.assertEqual(self.client.delete(path).status_code, 403)

    def test_device_registration_and_invalid_key(self):
        data = {'installation_uuid': str(uuid.uuid4()), 'display_name': 'New', 'public_key': 'invalid'}
        self.assertEqual(self.client.post('/api/devices/', data, format='json').status_code, 400)
        data['public_key'] = self.device.public_key
        response = self.client.post('/api/devices/', data, format='json')
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['status'], 'PENDING')
        self.assertEqual(self.client.post('/api/devices/', data, format='json').status_code, 400)

    def test_device_activation_requires_proof_not_uuid(self):
        self.assertEqual(self.client.post(f'/api/devices/{self.device.pk}/activate/',
                         {'installation_uuid': str(self.device.installation_uuid)}, format='json').status_code, 403)
        self.assertEqual(self.signed(f'/api/devices/{self.device.pk}/activate/',
                         key=Ed25519PrivateKey.generate()).status_code, 403)
        self.device.refresh_from_db()
        self.assertEqual(self.device.status, 'PENDING')

    def test_challenge_replay_expiry_and_body_tamper(self):
        path = f'/api/devices/{self.device.pk}/activate/'
        self.assertEqual(self.signed(path, tampered_body=b'{"changed":true}').status_code, 403)
        challenge = DeviceChallenge.objects.create(device=self.device, user=self.owner,
            purpose='ACTIVATE', expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.signed(path, challenge=challenge).status_code, 403)
        challenge.expires_at = timezone.now() + timedelta(minutes=1)
        challenge.save()
        self.assertEqual(self.signed(path, challenge=challenge).status_code, 200)
        self.assertEqual(self.signed(path, challenge=challenge).status_code, 403)

    def test_cross_tenant_device_and_challenge_denied(self):
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.post('/api/devices/challenge/', {'device_id': self.device.pk,
            'purpose': 'ACTIVATE'}, format='json').status_code, 404)
        self.assertEqual(self.signed(f'/api/devices/{self.device.pk}/activate/', user=self.other).status_code, 404)

    def test_double_primary_constraint_and_replace(self):
        self.enable()
        key = Ed25519PrivateKey.generate()
        second = self.new_device(key)
        self.assertEqual(self.signed(f'/api/devices/{second.pk}/activate/',
                                    device=second, key=key).status_code, 400)
        with self.assertRaises(IntegrityError), transaction.atomic():
            DeviceRegistration.objects.create(exploitation=self.farm, installation_uuid=uuid.uuid4(),
                display_name='Forbidden', public_key=second.public_key, status='ACTIVE', is_primary_writer=True)
        response = self.signed(f'/api/devices/{second.pk}/replace/', purpose='REPLACE', device=second, key=key)
        self.assertEqual(response.status_code, 200, response.data)
        self.device.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(self.device.status, 'REVOKED')
        self.assertEqual(second.write_generation, 2)
        self.assertEqual(DeviceRegistration.objects.filter(exploitation=self.farm,
                         status='ACTIVE', is_primary_writer=True).count(), 1)

    def test_operator_terrain_requires_active_primary_and_is_audited(self):
        self.enable()
        self.client.force_authenticate(self.jean)
        data = {'nom': 'Buyer', 'telephone': '123'}
        self.assertEqual(self.client.post('/api/clients/create/', data, format='json').status_code, 403)
        response = self.signed('/api/clients/create/', data, purpose='WRITE', user=self.jean)
        self.assertEqual(response.status_code, 201, response.data)
        buyer = Client.objects.get(pk=response.data['id'])
        self.assertEqual(buyer.created_by_id, self.jean.pk)
        event = AuditEvent.objects.get(entity_type='core.client')
        self.assertEqual(event.device_id, self.device.pk)
        self.client.force_authenticate(self.owner)
        self.assertEqual(self.client.post(f'/api/devices/{self.device.pk}/revoke/',
            {'reason': 'Lost'}, format='json').status_code, 200)
        self.assertEqual(self.signed('/api/clients/create/', data, purpose='WRITE', user=self.jean).status_code, 403)

    def test_audit_failure_rolls_back_business(self):
        with patch('core.audit.record', side_effect=RuntimeError('audit unavailable')):
            with self.assertRaises(RuntimeError):
                self.client.post('/api/clients/create/', {'nom': 'Rolled back', 'telephone': '1'}, format='json')
        self.assertFalse(Client.objects.filter(nom='Rolled back').exists())

    def test_audit_survives_delete_and_correlation(self):
        with audit_scope(ActorContext(self.owner.pk, self.farm.pk)):
            client = Client.objects.create(exploitation=self.farm, nom='Test', telephone='1')
            client.nom = 'Updated'
            client.save()
            pk = client.pk
            client.delete()
        events = AuditEvent.objects.filter(entity_type='core.client', entity_id=str(pk))
        self.assertEqual(events.count(), 3)
        self.assertEqual(len(set(events.values_list('correlation_id', flat=True))), 1)
        self.assertTrue(events.filter(action='DELETE').exists())

    def test_audit_immutable_api_admin_and_model(self):
        with audit_scope(ActorContext(self.owner.pk, self.farm.pk)):
            self.task()
        event = AuditEvent.objects.first()
        for mutate in (lambda: event.delete(), lambda: event.save(),
                       lambda: AuditEvent.objects.filter(pk=event.pk).update(action='BAD'),
                       lambda: AuditEvent.objects.filter(pk=event.pk).delete()):
            with self.assertRaises(ValidationError):
                mutate()
        self.assertEqual(self.client.post('/api/audit-events/', {}, format='json').status_code, 405)
        self.assertEqual(self.client.delete('/api/audit-events/').status_code, 405)
        request = RequestFactory().get('/admin/')
        request.user = User.objects.create_superuser(username='root')
        modeladmin = admin.site._registry[AuditEvent]
        self.assertFalse(modeladmin.has_change_permission(request, event))
        self.assertFalse(modeladmin.has_delete_permission(request, event))

    def test_secrets_are_filtered_recursively_and_payload_bounded(self):
        self.assertEqual(safe_data({'password': 'x', 'nested': {'refresh_token': 'x', 'title': 'ok'},
                                   'private_key': 'x', 'pin': '12'}), {'nested': {'title': 'ok'}})
        with self.assertRaises(ValidationError):
            AuditEvent.objects.create(exploitation_id=self.farm.pk, action='X', entity_type='x',
                                      entity_id='1', metadata={'text': 'x' * 65537})

    def test_audit_tenant_read_isolation(self):
        with audit_scope(ActorContext(self.other.pk, self.other.exploitation_id)):
            Task.objects.create(exploitation=self.other.exploitation, title='Private', date='2026-10-06')
        response = self.client.get('/api/audit-events/?entity_type=core.task')
        self.assertEqual(response.data['count'], 0)

    def test_composite_egg_sale_fifo_authors_and_delete_audit(self):
        from core.models import Vente, VenteOeufs
        species = Espece.objects.create(exploitation=self.farm, nom='Poulet')
        lot = Lot.objects.create(exploitation=self.farm, espece=species, nom='Pondeuses',
            date_debut=timezone.localdate() - timedelta(days=30), type_production='OEUFS')
        buyer = Client.objects.create(exploitation=self.farm, nom='Client')
        collection = self.client.post('/api/oeufs/collectes/', {'lot': lot.pk,
            'nombre_collecte': 100, 'collecte_at': (timezone.now() - timedelta(days=1)).isoformat()}, format='json')
        self.assertEqual(collection.status_code, 201, collection.data)
        sale = self.client.post('/api/oeufs/ventes/', {'lot': lot.pk, 'client': buyer.pk,
            'date': timezone.localdate().isoformat(), 'conditionnement': 'COMPOSE',
            'nombre_alveoles': 1, 'oeufs_supplementaires': 0, 'prix_total': '300.00'}, format='json')
        self.assertEqual(sale.status_code, 201, sale.data)
        obj = VenteOeufs.objects.get(pk=sale.data['id'])
        self.assertEqual(obj.created_by_id, self.owner.pk)
        self.assertEqual(obj.vente.created_by_id, self.owner.pk)
        event = AuditEvent.objects.get(entity_type='core.venteoeufs', entity_id=str(obj.pk), action='CREATE')
        events = AuditEvent.objects.filter(correlation_id=event.correlation_id)
        self.assertEqual(set(events.values_list('entity_type', flat=True)), {
            'core.vente', 'core.venteoeufs', 'core.mouvementoeufs', 'core.affectationmouvementoeufs'})
        result = self.client.delete(f'/api/oeufs/ventes/{obj.pk}/')
        self.assertEqual(result.status_code, 204, result.data)
        deleted = AuditEvent.objects.filter(action='DELETE', entity_type='core.affectationmouvementoeufs')
        self.assertTrue(deleted.exists())

    def test_expired_stale_rights_and_generation_grants_rejected(self):
        self.enable()
        self.device.refresh_from_db()
        member = self.jean.memberships.get(exploitation=self.farm)
        grant = OfflineAuthorization.objects.create(membership=member, device=self.device, capabilities={},
            rights_version=member.version, write_generation=self.farm.write_generation,
            expires_at=timezone.now() - timedelta(seconds=1))
        def validate():
            return validated_offline_context(authorization_id=grant.pk, verified_device=self.device,
                                             transport_identity='DEVICE')
        with self.assertRaises(PermissionDenied):
            validate()
        grant.expires_at = timezone.now() + timedelta(days=1)
        grant.rights_version += 1
        grant.save()
        with self.assertRaises(PermissionDenied):
            validate()
        grant.rights_version = member.version
        grant.write_generation += 1
        grant.save()
        with self.assertRaises(PermissionDenied):
            validate()

    def test_inactive_member_refresh_stays_blacklisted_after_reactivation(self):
        self.jean.set_password('Operator-test-unique-400!')
        self.jean.save()
        self.client.force_authenticate(None)
        login = self.client.post('/api/token/', {'username': 'jean', 'password': 'Operator-test-unique-400!'}, format='json')
        self.assertEqual(login.status_code, 200)
        refresh = login.data['refresh']
        self.client.force_authenticate(self.owner)
        member = self.jean.memberships.get(exploitation=self.farm)
        self.client.patch(f'/api/memberships/{member.pk}/', {'is_active': False}, format='json')
        self.client.force_authenticate(None)
        self.assertEqual(self.client.post('/api/token/refresh/', {'refresh': refresh}, format='json').status_code, 401)
        self.client.force_authenticate(self.owner)
        self.client.patch(f'/api/memberships/{member.pk}/', {'is_active': True}, format='json')
        self.client.force_authenticate(None)
        self.assertEqual(self.client.post('/api/token/refresh/', {'refresh': refresh}, format='json').status_code, 401)

    def test_existing_user_tenant_transfer_is_refused(self):
        self.jean.exploitation = self.other.exploitation
        with self.assertRaises(ValidationError):
            self.jean.save()
        self.jean.refresh_from_db()
        self.assertEqual(self.jean.exploitation_id, self.farm.pk)

    def test_offline_grant_missing_key_fails_closed(self):
        self.enable()
        self.client.force_authenticate(self.jean)
        with override_settings(OFFLINE_SIGNING_PRIVATE_KEY=''):
            self.assertEqual(self.client.post('/api/offline-authorizations/', {}, format='json').status_code, 503)
        self.assertEqual(OfflineAuthorization.objects.count(), 0)

    def test_signed_offline_grant_author_transport_and_revocation(self):
        self.enable()
        serverkey = Ed25519PrivateKey.generate()
        pem = serverkey.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                      serialization.NoEncryption()).decode()
        with override_settings(OFFLINE_SIGNING_PRIVATE_KEY=pem, OFFLINE_AUTHORIZATION_DAYS=7):
            response = self.signed('/api/offline-authorizations/', purpose='GRANT', user=self.jean)
        self.assertEqual(response.status_code, 201, response.data)
        payload = jwt.decode(response.data['authorization'], serverkey.public_key(), algorithms=['EdDSA'],
                             audience='elevage-device', issuer='elevage-offline')
        self.assertEqual(payload['exp'] - payload['iat'], 7 * 86400)
        grant = OfflineAuthorization.objects.get(pk=payload['jti'])
        context = validated_offline_context(authorization_id=grant.pk, verified_device=self.device,
                                             transport_identity='DEVICE', decision_actor_id=self.owner.pk)
        self.assertEqual(context.author_id, self.jean.pk)
        self.assertEqual(context.decision_actor_id, self.owner.pk)
        self.client.force_authenticate(self.owner)
        member = self.jean.memberships.get(exploitation=self.farm)
        self.client.patch(f'/api/memberships/{member.pk}/', {'is_active': False}, format='json')
        with self.assertRaises(PermissionDenied):
            validated_offline_context(authorization_id=grant.pk, verified_device=self.device, transport_identity='DEVICE')

    def test_refresh_compatible_logout_blacklist_and_inactive_member(self):
        self.client.force_authenticate(None)
        login = self.client.post('/api/token/', {'username': 'owner', 'password': 'Strong-test-2091!'}, format='json')
        self.assertEqual(login.status_code, 200)
        refresh = login.data['refresh']
        self.assertEqual(self.client.post('/api/token/refresh/', {'refresh': refresh}, format='json').status_code, 200)
        self.client.force_authenticate(self.owner)
        self.assertEqual(self.client.post('/api/token/logout/', {'refresh': refresh}, format='json').status_code, 204)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.post('/api/token/refresh/', {'refresh': refresh}, format='json').status_code, 401)

    def test_postgresql_audit_trigger(self):
        if connection.vendor != 'postgresql':
            self.skipTest('PostgreSQL database required; SQLite does not validate this trigger.')
        event = AuditEvent.objects.create(exploitation_id=self.farm.pk, action='X', entity_type='x', entity_id='1')
        from django.db import DatabaseError
        with self.assertRaises(DatabaseError), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute('UPDATE core_auditevent SET action = %s WHERE id = %s', ['BAD', event.pk])
