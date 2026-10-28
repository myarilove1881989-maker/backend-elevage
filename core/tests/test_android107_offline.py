"""Android 107 preparation contract and production activation prerequisites."""
import jwt
import io
from contextlib import redirect_stdout
from unittest.mock import patch
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from django.test import SimpleTestCase, override_settings
from config.release_security import production_security
from core.tests.test_mobile_foundations import MobileFoundationsTests


class ActivationTests(SimpleTestCase):
    def config(self):
        key = Ed25519PrivateKey.generate()
        return {'OFFLINE_FINE_PERMISSIONS_ENABLED': 'true',
            'OFFLINE_FINE_AUTHORIZATION_HOURS': '168', 'OFFLINE_SIGNING_KEY_ID': 'synthetic-v2',
            'OFFLINE_SIGNING_PRIVATE_KEY': key.private_bytes(serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()}

    def test_opt_in_requires_key_identity_and_bounded_window(self):
        production_security({})
        production_security(self.config())
        for field, value in [('OFFLINE_FINE_PERMISSIONS_ENABLED', 'yes'),
                ('OFFLINE_SIGNING_PRIVATE_KEY', ''), ('OFFLINE_SIGNING_PRIVATE_KEY', 'invalid'),
                ('OFFLINE_SIGNING_KEY_ID', ''), ('OFFLINE_FINE_AUTHORIZATION_HOURS', '0'),
                ('OFFLINE_FINE_AUTHORIZATION_HOURS', '169'), ('OFFLINE_FINE_AUTHORIZATION_HOURS', 'abc')]:
            with self.subTest(field=field, value=value):
                config = self.config(); config[field] = value
                with self.assertRaises(ValueError): production_security(config)


class Android107PreparationTests(MobileFoundationsTests):
    @override_settings(OFFLINE_FINE_PERMISSIONS_ENABLED=True)
    def test_rotation_between_authentication_and_farm_lock_refuses_issuance(self):
        from core.models import User, OfflineAuthorization
        self.enable()
        server = Ed25519PrivateKey.generate()
        pem = server.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption()).decode()
        original = self.jean.refresh_from_db
        def rotated():
            User.objects.filter(pk=self.jean.pk).update(credential_version=2)
            original()
        with override_settings(OFFLINE_SIGNING_PRIVATE_KEY=pem), patch.object(
                self.jean, 'refresh_from_db', side_effect=rotated):
            result = self.signed('/api/offline-authorizations/',
                {'permission_contract_version': 2}, purpose='GRANT', user=self.jean)
        self.assertEqual(result.status_code, 403, result.data)
        self.assertEqual(str(result.data['detail']), 'SESSION_REVOKED')
        self.assertFalse(OfflineAuthorization.objects.exists())

    @override_settings(OFFLINE_FINE_PERMISSIONS_ENABLED=True)
    def test_personal_epoch_and_v2_permissions_are_signed_and_rotation_revokes(self):
        from core.models import OfflineAuthorization
        from core.audit import ActorContext, audit_scope
        from core.credential_services import rotate_credentials
        self.enable()
        server = Ed25519PrivateKey.generate()
        pem = server.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption()).decode()
        self.client.force_authenticate(self.jean)
        caps = self.client.get('/api/me/capabilities/').data
        self.assertEqual(caps['credential_version'], self.jean.credential_version)
        self.assertTrue(caps['offline_permission_contract']['issuance_enabled'])
        with override_settings(OFFLINE_SIGNING_PRIVATE_KEY=pem):
            result = self.signed('/api/offline-authorizations/',
                {'permission_contract_version': 2}, purpose='GRANT', user=self.jean)
        self.assertEqual(result.status_code, 201, result.data)
        claims = jwt.decode(result.data['authorization'], server.public_key(), algorithms=['EdDSA'],
            audience='elevage-device', issuer='elevage-offline')
        self.assertEqual(claims['credential_version'], caps['credential_version'])
        self.assertEqual(claims['capabilities']['permission_contract']['version'], 2)
        self.assertNotIn('users.manage', claims['capabilities']['permission_contract']['permissions'])
        with audit_scope(ActorContext(self.owner.pk, self.farm.pk)):
            rotate_credentials(self.jean, password='synthetic-rotated-credentials')
        grant = OfflineAuthorization.objects.get(pk=claims['jti'])
        self.assertEqual(grant.revocation_kind, 'SECURITY')
        self.assertIsNotNone(grant.revoked_at)
        self.assertEqual(self.jean.credential_version, caps['credential_version'] + 1)

    def test_startup_proof_never_prints_key_or_token(self):
        from core.offline_readiness import report
        server = Ed25519PrivateKey.generate()
        pem = server.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption()).decode()
        output = io.StringIO()
        with override_settings(OFFLINE_SIGNING_PRIVATE_KEY=pem), redirect_stdout(output):
            report()
        self.assertIn('"ed25519_roundtrip_verified": true', output.getvalue())
        self.assertNotIn('PRIVATE KEY', output.getvalue())
        self.assertNotIn(pem, output.getvalue())
