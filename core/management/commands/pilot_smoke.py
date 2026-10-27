"""Real PostgreSQL checks on the pilot; TestCase transactions roll back fixtures."""
import json
import os
import hashlib
import unittest
import jwt
from cryptography.hazmat.primitives import serialization
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from core.pilot_checks import verify_database


class Command(BaseCommand):
    help = 'Validate pilot HTTPS/JWT plus transactional enrolment and synchronization regression checks.'

    def handle(self, *args, **options):
        identity = verify_database()
        self.stdout.write('PILOT_SMOKE_IDENTITY ' + json.dumps(identity, sort_keys=True))
        host = settings.ALLOWED_HOSTS[0]
        client = APIClient(HTTP_HOST=host)
        if client.get('/api/pilot-health/', secure=True).status_code != 200:
            raise CommandError('PILOT_SMOKE: HTTPS health failed')
        if client.get('/api/pilot-health/').status_code != 301:
            raise CommandError('PILOT_SMOKE: HTTP upgrade failed')
        if client.get('/api/pilot-health/', secure=True, HTTP_HOST='backend-elevage.onrender.com').status_code != 400:
            raise CommandError('PILOT_SMOKE: production host accepted')
        login = client.post('/api/token/', {'username':'pilot-owner',
            'password':os.environ['PILOT_OWNER_PASSWORD']}, format='json', secure=True)
        if login.status_code != 200:
            raise CommandError('PILOT_SMOKE: login failed')
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + login.data['access'])
        if client.get('/api/me/capabilities/', secure=True).status_code != 200:
            raise CommandError('PILOT_SMOKE: authenticated capabilities failed')
        payload = jwt.decode(login.data['access'], settings.SECRET_KEY, algorithms=['HS256'])
        foreign = jwt.encode(payload, 'synthetic-foreign-key-not-production-0123456789', algorithm='HS256')
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + foreign)
        if client.get('/api/me/capabilities/', secure=True).status_code != 401:
            raise CommandError('PILOT_SMOKE: foreign JWT accepted')
        from core.models import User, DeviceRegistration, TerrainSubmission
        before = (User.objects.count(), DeviceRegistration.objects.count(), TerrainSubmission.objects.count())
        from core.tests.test_mobile_foundations import MobileFoundationsTests
        from core.tests.test_device_p256 import AndroidKeystoreProofTests
        from core.tests.test_terrain_transport import TerrainTransportTests
        classes = [MobileFoundationsTests, AndroidKeystoreProofTests, TerrainTransportTests]
        if any(not issubclass(cls, TestCase) for cls in classes):
            raise CommandError('PILOT_SMOKE: nontransactional test refused')
        suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromTestCase(cls) for cls in classes)
        # Exercise the deployed server key through the real grant endpoint,
        # without replacing OFFLINE_SIGNING_PRIVATE_KEY with a test key.
        configured_grant = MobileFoundationsTests('runTest')
        def check_configured_grant():
            configured_grant.enable()
            response = configured_grant.signed('/api/offline-authorizations/',
                user=configured_grant.jean, purpose='GRANT')
            configured_grant.assertEqual(response.status_code, 201)
            key = serialization.load_pem_private_key(settings.OFFLINE_SIGNING_PRIVATE_KEY.encode(), password=None)
            public = key.public_key().public_bytes(serialization.Encoding.PEM,
                serialization.PublicFormat.SubjectPublicKeyInfo).decode('ascii')
            configured_grant.assertEqual(response.data['public_key'], public)
            configured_grant.assertEqual(response.data['key_id'], 'pilot-ed25519-v1')
            claims = jwt.decode(response.data['authorization'], key.public_key(), algorithms=['EdDSA'],
                audience='elevage-device', issuer='elevage-offline')
            configured_grant.assertEqual(claims['sub'], str(configured_grant.jean.pk))
        configured_grant.runTest = check_configured_grant
        suite.addTest(configured_grant)
        # These existing tests simulate local HTTP. Actual HTTPS is checked above.
        with override_settings(SECURE_SSL_REDIRECT=False, ALLOWED_HOSTS=[host, 'testserver']):
            result = unittest.TextTestRunner(stream=self.stdout, verbosity=1).run(suite)
        after = (User.objects.count(), DeviceRegistration.objects.count(), TerrainSubmission.objects.count())
        if not result.wasSuccessful() or after != before:
            raise CommandError('PILOT_SMOKE: regression or fixture rollback failure')
        self.stdout.write('PILOT_SMOKE_COMPLETE ' + json.dumps({'tests':result.testsRun,
            'https':True, 'production_host_rejected':True, 'foreign_jwt_rejected':True,
            'enrolment_p256':True, 'offline_grants':True, 'synchronization':True,
            'transactional_fixtures_rolled_back':True,
            'configured_server_ed25519_grant':True,
            'server_public_key_sha256':hashlib.sha256(serialization.load_pem_private_key(
                settings.OFFLINE_SIGNING_PRIVATE_KEY.encode(), password=None).public_key().public_bytes(
                    serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)).hexdigest()}, sort_keys=True))
