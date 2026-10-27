"""Regression: a populated pilot must not be treated as an empty test database."""
import io
import os
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from django.core.management import call_command
from django.test import TestCase, override_settings

from core.models import User, DeviceRegistration, TerrainSubmission
from core.tests.test_terrain_transport import TransportFixture


class PopulatedPilotSmokeTests(TransportFixture, TestCase):
    @override_settings(
        ALLOWED_HOSTS=['backend-elevage-pilot.onrender.com'],
        ROOT_URLCONF='config.pilot_urls', SECURE_SSL_REDIRECT=True,
        EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
        OFFLINE_SIGNING_KEY_ID='pilot-ed25519-v1',
    )
    def test_repeated_smoke_preserves_existing_terrain_rows(self):
        # Populate the same tables that caused global count/get assertions to fail.
        with override_settings(SECURE_SSL_REDIRECT=False, ALLOWED_HOSTS=['testserver']):
            response, _ = self.send(self.operation())
        self.assertEqual(response.status_code, 200, response.data)
        password = 'Synthetic-pilot-probe-password-only-0123456789'
        owner = User.objects.create_user(username='pilot-owner', password='Owner-password-changed-by-user-0123456789')
        User.objects.create_user(username='pilot-operator', password=password, exploitation=owner.exploitation)
        key = Ed25519PrivateKey.generate().private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption()).decode('ascii')
        row = TerrainSubmission.objects.get()
        before = (User.objects.count(), DeviceRegistration.objects.count(), TerrainSubmission.objects.count())
        with patch.dict(os.environ, PILOT_OWNER_PASSWORD='old-owner-password-no-longer-valid', PILOT_OPERATOR_PASSWORD=password), \
             patch('core.management.commands.pilot_smoke.verify_database', return_value={'local_test': True}), \
             override_settings(OFFLINE_SIGNING_PRIVATE_KEY=key):
            for _ in range(2):
                output = io.StringIO()
                call_command('pilot_smoke', stdout=output)
                self.assertIn('PILOT_SMOKE_COMPLETE', output.getvalue())
                self.assertIn('dedicated_test_database_only', output.getvalue())
                self.assertEqual(before, (User.objects.count(), DeviceRegistration.objects.count(), TerrainSubmission.objects.count()))
                row.refresh_from_db()
                self.assertEqual(row.author_user_id, self.jean.pk)
