"""Synthetic PostgreSQL + loopback HTTP evidence, without DRF's in-process client.

PIN/SQLCipher lifecycle is verified separately in Flutter; this is not an Android
or a Flutter-to-Django end-to-end test. No external host is contacted.
"""
import base64
import hashlib
import json
from datetime import timedelta
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, build_opener, ProxyHandler

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from django.test import LiveServerTestCase, TransactionTestCase, skipUnlessDBFeature
from django.utils import timezone
from rest_framework_simplejwt.tokens import AccessToken

from core.access_policy import effective_permissions
from core.models import Client, MembershipPermission, OfflineAuthorization, TerrainSubmission, Vente
from core.offline_permissions import contract_for
from core.tests.test_terrain_operations import OperationFixture
from core.tests import test_offline_simple_mission as mission


class LoopbackClient:
    def __init__(self, base, user=None):
        self.base = base
        self.token = str(AccessToken.for_user(user)) if user else None
        self.opener = build_opener(ProxyHandler({}))

    def generic(self, method, path, body=None, content_type='application/json', **headers):
        actual = {key.removeprefix('HTTP_').replace('_', '-'): value for key, value in headers.items()}
        if self.token:
            actual['Authorization'] = 'Bearer ' + self.token
        actual['Content-Type'] = content_type
        request = Request(self.base + path, data=body, headers=actual, method=method)
        try:
            response = self.opener.open(request, timeout=20)
        except HTTPError as error:
            response = error
        with response:
            raw = response.read()
            try:
                data = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError):
                content_type = response.headers.get('Content-Type', '<missing>')
                raise AssertionError(
                    f'{method} {path} returned HTTP {response.code} '
                    f'with Content-Type {content_type}; expected JSON'
                ) from None
            return SimpleNamespace(status_code=response.code, data=data)

    def get(self, path, data=None):
        return self.generic('GET', path + ('?' + urlencode(data) if data else ''))

    def patch(self, path, data, format=None):
        return self.generic('PATCH', path, json.dumps(data).encode())


class OfflineLiveHttpTests(OperationFixture, LiveServerTestCase):
    sale = mission.SimpleOfflineMissionTests.sale
    cancel = mission.SimpleOfflineMissionTests.cancel
    history = mission.SimpleOfflineMissionTests.history
    ordinary_change = mission.SimpleOfflineMissionTests.ordinary_change

    def setUp(self):
        super().setUp()
        self.jean.first_name = 'Paul'; self.jean.save()
        self.paul.first_name = 'Jean'; self.paul.save()
        self.customer = Client.objects.create(exploitation=self.farm, nom='Client fictif HTTP')
        self.grant.capabilities['permission_contract'] = contract_for(self.grant, effective_permissions(self.member))
        self.grant.save()
        self.http = LoopbackClient(self.live_server_url)
        self.owner_api = LoopbackClient(self.live_server_url, self.owner)

    def send(self, operation, *, purpose='RECEIVE', invalid_signature=False):
        challenge = self.http.generic('POST', '/api/offline/transport-challenge/', json.dumps({
            'device_id':self.device.pk, 'installation_uuid':str(self.device.installation_uuid),
            'purpose':purpose}).encode())
        self.assertEqual(challenge.status_code,201,challenge.data)
        path = '/api/offline/submissions/' + ('status/' if purpose == 'STATUS' else '')
        data = {'operations':[operation]} if purpose == 'RECEIVE' else {'operation_ids':[operation['client_operation_id']]}
        body = json.dumps(data,separators=(',',':')).encode()
        message = (f'ELEVAGE-DEVICE-TRANSPORT-V1\n{challenge.data["id"]}\n{self.device.pk}\n{self.farm.pk}\n'
                   f'1\n{purpose}\nPOST\n{path}\n{hashlib.sha256(body).hexdigest()}').encode()
        key = ec.generate_private_key(ec.SECP256R1()) if invalid_signature else self.key
        signature = key.sign(message,ec.ECDSA(hashes.SHA256()))
        return self.http.generic('POST',path,body,
            HTTP_AUTHORIZATION='Bearer deliberately-expired-personal-session',
            HTTP_X_ELEVAGE_DEVICE=str(self.device.pk),HTTP_X_ELEVAGE_CHALLENGE=challenge.data['id'],
            HTTP_X_ELEVAGE_SIGNATURE=base64.b64encode(signature).decode()), challenge

    def test_sale_ten_pigs_then_cancellation_and_lost_response_retries(self):
        original=self.sale();original['payload']['quantite']=10
        self.assertFalse(TerrainSubmission.objects.exists())  # Not yet transmitted.
        first=self.apply(original);self.assertEqual(first['business_status'],'CONFIRMED')
        self.apply(original);self.assertEqual(self.lot.stock,10);self.assertEqual(Vente.objects.count(),1)
        cancellation=self.cancel(original)
        self.assertEqual(self.apply(cancellation)['business_status'],'CONFIRMED')
        self.apply(cancellation);self.assertEqual(self.lot.stock,20)
        rows=self.history();self.assertEqual(len(rows),2)
        source=next(row for row in rows if row['operation_type']=='CREATE')
        self.assertEqual(source['business_status'],'SUPERSEDED')
        self.assertEqual(source['cancellations'][0]['operation_id'],cancellation['client_operation_id'])
        self.assertEqual(TerrainSubmission.objects.get(client_operation_id=original['client_operation_id']).payload,original['payload'])

    def test_business_rights_refresh_preserves_other_employee(self):
        other=self.paul.memberships.get(exploitation=self.farm)
        MembershipPermission.objects.create(membership=other,code='animal_sales.create')
        OfflineAuthorization.objects.create(membership=other,device=self.device,rights_version=other.version,
            write_generation=1,issued_at=self.grant.issued_at,expires_at=self.grant.expires_at)
        original=self.sale();self.ordinary_change()
        self.assertEqual(self.apply(original)['business_status'],'CONFIRMED')
        response,_=self.send(original,purpose='STATUS')
        states={row['user_id']:row for row in response.data['profiles']}
        self.assertNotIn('animal_sales.create',states[self.jean.pk]['permissions'])
        self.assertIn('animal_sales.create',states[self.paul.pk]['permissions'])
        self.assertTrue(self.history()[0]['received_with_old_permissions'])

    def test_security_disabled_account_invalid_signature_and_revoked_device(self):
        response,_=self.send(self.sale(),invalid_signature=True)
        self.assertEqual(response.status_code,403);self.assertFalse(TerrainSubmission.objects.exists())
        self.jean.is_active=False;self.jean.save()
        self.assertEqual(self.apply(self.sale())['business_status'],'NEEDS_RECONCILIATION')
        self.device.status='REVOKED';self.device.is_primary_writer=False;self.device.save()
        response=self.http.generic('POST','/api/offline/transport-challenge/',json.dumps({
            'device_id':self.device.pk,'installation_uuid':str(self.device.installation_uuid),'purpose':'RECEIVE'}).encode())
        self.assertEqual(response.status_code,403);self.assertFalse(Vente.objects.exists())

    def test_history_without_lot_keeps_refused_anomalies_and_tenant_isolation(self):
        original = self.sale()
        original['payload']['quantite'] = 100
        self.ordinary_change()
        self.assertEqual(self.apply(original)['reason_code'], 'STOCK_INSUFFICIENT')
        start = (timezone.now() - timedelta(days=1)).isoformat()
        end = (timezone.now() + timedelta(days=1)).isoformat()
        rows = self.history(author=self.jean.pk, status='NEEDS_RECONCILIATION',
                            start=start, end=end)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertTrue(row['received_with_old_permissions'])
        self.assertIsNone(row['applied_at'])
        self.assertEqual(row['operation_id'], original['client_operation_id'])
        self.assertEqual(row['actor_user_id'], self.jean.pk)
        self.assertTrue(row['received_at'])
        self.assertTrue(row['local_recorded_at'])
        self.assertEqual(self.history(author=self.paul.pk), [])
        self.assertEqual(self.history(status='CONFIRMED'), [])
        self.assertEqual(self.history(start=end), [])
        self.assertEqual(self.history(end=start), [])
        foreign = type(self.owner).objects.create_user(username='http-foreign-owner-without-lot')
        response = LoopbackClient(self.live_server_url, foreign).get('/api/history/', {
            'view': 'operations', 'operation': original['client_operation_id'],
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['results'], [])
        response = LoopbackClient(self.live_server_url, self.jean).get('/api/history/', {
            'view': 'operations',
        })
        self.assertEqual(response.status_code, 403)

    @skipUnlessDBFeature('supports_json_field_contains')
    def test_history_refused_anomalies_filters_and_tenant_isolation(self):
        original=self.sale();original['payload']['quantite']=100;self.ordinary_change()
        self.assertEqual(self.apply(original)['reason_code'],'STOCK_INSUFFICIENT')
        rows=self.history(author=self.jean.pk,lot=self.lot.pk,status='NEEDS_RECONCILIATION')
        self.assertEqual(len(rows),1)
        row=rows[0];self.assertTrue(row['received_with_old_permissions']);self.assertIsNone(row['applied_at'])
        self.assertEqual(row['operation_id'],original['client_operation_id'])
        self.assertTrue(row['received_at']);self.assertTrue(row['local_recorded_at'])
        self.assertEqual(self.history(author=self.paul.pk),[])
        foreign=type(self.owner).objects.create_user(username='http-foreign-owner')
        response=LoopbackClient(self.live_server_url,foreign).get('/api/history/',{'view':'operations','operation':original['client_operation_id']})
        self.assertEqual(response.status_code,200);self.assertEqual(response.data['results'],[])


class OfflineMigrationConsolidationTests(OperationFixture, TransactionTestCase):
    def test_0037_preserves_old_revocations_and_never_assigns_cancel_permissions(self):
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor
        from django.utils import timezone
        MembershipPermission.objects.filter(code__endswith='.cancel').delete()
        self.grant.revoked_at=timezone.now();self.grant.save()
        before=list(MembershipPermission.objects.order_by('pk').values_list('membership_id','code'))
        try:
            MigrationExecutor(connection).migrate([('core','0036_expense_cancellation_immutable')])
            MigrationExecutor(connection).migrate([('core','0037_offline_business_revocation')])
            self.grant.refresh_from_db()
            self.assertIsNotNone(self.grant.revoked_at)
            self.assertEqual(self.grant.revocation_kind,'')
            self.assertIsNone(self.grant.business_through_version)
            self.assertEqual(list(MembershipPermission.objects.order_by('pk').values_list('membership_id','code')),before)
            self.assertFalse(MembershipPermission.objects.filter(code__endswith='.cancel').exists())
        finally:
            executor=MigrationExecutor(connection)
            executor.migrate(executor.loader.graph.leaf_nodes('core'))
