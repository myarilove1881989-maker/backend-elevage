import base64
import hashlib
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from django.db import connection, close_old_connections
from django.test import TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient
from core.models import User, DeviceRegistration, DeviceChallenge


class PrimaryActivationConcurrencyTests(TransactionTestCase):
    def test_two_activations_serialize_on_postgresql(self):
        if connection.vendor != 'postgresql':
            self.skipTest('PostgreSQL required for concurrent row-lock activation validation.')
        owner = User.objects.create_user(username='concurrent-owner')
        barrier = Barrier(2, timeout=20)
        candidates = []
        for _ in range(2):
            key = Ed25519PrivateKey.generate()
            device = DeviceRegistration.objects.create(exploitation=owner.exploitation,
                installation_uuid=uuid.uuid4(), display_name='Concurrent',
                public_key=key.public_key().public_bytes(serialization.Encoding.PEM,
                    serialization.PublicFormat.SubjectPublicKeyInfo).decode())
            challenge = DeviceChallenge.objects.create(device=device, user=owner, purpose='ACTIVATE',
                expires_at=timezone.now() + timedelta(minutes=5))
            candidates.append((key, device.pk, str(challenge.pk)))

        def activate(candidate):
            close_old_connections()
            key, device_id, challenge_id = candidate
            try:
                client = APIClient()
                user = User.objects.get(pk=owner.pk)
                client.force_authenticate(user)
                path = f'/api/devices/{device_id}/activate/'
                body = b'{}'
                message = (f'ELEVAGE-DEVICE-V1\n{challenge_id}\n{device_id}\n{owner.pk}\nACTIVATE\n'
                           f'POST\n{path}\n{hashlib.sha256(body).hexdigest()}').encode()
                barrier.wait()
                response = client.generic('POST', path, body, content_type='application/json',
                    HTTP_X_ELEVAGE_DEVICE=str(device_id), HTTP_X_ELEVAGE_CHALLENGE=challenge_id,
                    HTTP_X_ELEVAGE_SIGNATURE=base64.b64encode(key.sign(message)).decode())
                return response.status_code
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(activate, candidates))
        self.assertEqual(sorted(results), [200, 400])
        self.assertEqual(DeviceRegistration.objects.filter(exploitation=owner.exploitation,
                         status='ACTIVE', is_primary_writer=True).count(), 1)
