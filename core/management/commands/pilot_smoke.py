"""Pilot checks and one rollback-only server-key probe, safe with existing rows.

Run the full regression suite with Django's test runner on a dedicated database,
never directly on the pilot's populated database.
"""
import json
import os
import hashlib
import jwt
from cryptography.hazmat.primitives import serialization
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from core.pilot_checks import verify_database


class Command(BaseCommand):
    help = 'Validate pilot HTTPS/JWT and the configured signing key without running global regression fixtures.'

    @override_settings(PILOT_RESET_MAIL_AUTOMATIC=False)
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
        # The owner's password is user-controlled after a password reset.
        # Exercise login with the unchanged fictitious operator instead.
        login = client.post('/api/token/', {'username':'pilot-operator',
            'password':os.environ['PILOT_OPERATOR_PASSWORD']}, format='json', secure=True)
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
        if not issubclass(MobileFoundationsTests, TestCase):
            raise CommandError('PILOT_SMOKE: nontransactional probe refused')
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
        # Do not invoke TestCase.run/tearDownClass on the service connection:
        # Django's test lifecycle can close it. Own the rollback explicitly.
        with override_settings(SECURE_SSL_REDIRECT=False, ALLOWED_HOSTS=[host, 'testserver']), transaction.atomic():
            configured_grant.setUp()
            try:
                check_configured_grant()
            except AssertionError:
                raise CommandError('PILOT_SMOKE: configured signing key probe failed') from None
            finally:
                transaction.set_rollback(True)
        after = (User.objects.count(), DeviceRegistration.objects.count(), TerrainSubmission.objects.count())
        if after != before:
            raise CommandError('PILOT_SMOKE: regression or fixture rollback failure')
        self.stdout.write('PILOT_SMOKE_COMPLETE ' + json.dumps({'configured_key_probes':1,
            'https':True, 'production_host_rejected':True, 'foreign_jwt_rejected':True,
            'regression_suite':'dedicated_test_database_only',
            'transactional_fixtures_rolled_back':True,
            'configured_server_ed25519_grant':True,
            'server_public_key_sha256':hashlib.sha256(serialization.load_pem_private_key(
                settings.OFFLINE_SIGNING_PRIVATE_KEY.encode(), password=None).public_key().public_bytes(
                    serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)).hexdigest()}, sort_keys=True))
