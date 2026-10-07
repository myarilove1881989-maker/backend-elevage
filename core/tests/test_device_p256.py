import base64
import hashlib
import uuid
from datetime import timedelta

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from django.test import TestCase
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from core.device_services import load_public_key
from core.models import DeviceChallenge, DeviceRegistration, User


class AndroidKeystoreProofTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username='keystore-owner')
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.device = DeviceRegistration.objects.create(
            exploitation=self.owner.exploitation, installation_uuid=uuid.uuid4(),
            display_name='Android API 24', public_key=self.pem(self.key))
        self.client = APIClient()
        self.client.force_authenticate(self.owner)

    @staticmethod
    def pem(key):
        return key.public_key().public_bytes(serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo).decode('ascii')

    def activate(self, *, wrong_key=False, tamper=False, challenge=None):
        challenge = challenge or DeviceChallenge.objects.create(
            device=self.device, user=self.owner, purpose='ACTIVATE',
            expires_at=timezone.now() + timedelta(minutes=5))
        path = f'/api/devices/{self.device.pk}/activate/'
        body = b'{}'
        message = (f'ELEVAGE-DEVICE-V1\n{challenge.pk}\n{self.device.pk}\n'
                   f'{self.owner.pk}\nACTIVATE\nPOST\n{path}\n'
                   f'{hashlib.sha256(body).hexdigest()}').encode()
        key = ec.generate_private_key(ec.SECP256R1()) if wrong_key else self.key
        signature = key.sign(message, ec.ECDSA(hashes.SHA256()))
        return self.client.generic('POST', path, b'{"changed":true}' if tamper else body,
            content_type='application/json', HTTP_X_ELEVAGE_DEVICE=str(self.device.pk),
            HTTP_X_ELEVAGE_CHALLENGE=str(challenge.pk),
            HTTP_X_ELEVAGE_SIGNATURE=base64.b64encode(signature).decode()), challenge

    def test_android_der_signature_activates_and_cannot_replay(self):
        response, challenge = self.activate()
        self.assertEqual(response.status_code, 200, response.data)
        self.device.refresh_from_db()
        self.assertTrue(self.device.is_primary_writer)
        response, _ = self.activate(challenge=challenge)
        self.assertEqual(response.status_code, 403)

    def test_wrong_key_and_changed_body_cannot_activate(self):
        for options in ({'wrong_key': True}, {'tamper': True}):
            response, challenge = self.activate(**options)
            self.assertEqual(response.status_code, 403)
            challenge.refresh_from_db()
            self.assertIsNone(challenge.consumed_at)
        self.device.refresh_from_db()
        self.assertFalse(self.device.is_primary_writer)

    def test_reject_unsupported_curve_and_rsa(self):
        for key in (ec.generate_private_key(ec.SECP384R1()),
                    rsa.generate_private_key(public_exponent=65537, key_size=2048)):
            with self.assertRaises(ValidationError):
                load_public_key(self.pem(key))

    def test_registration_accepts_p256_public_key(self):
        response = self.client.post('/api/devices/', {
            'installation_uuid': str(uuid.uuid4()), 'display_name': 'Keystore',
            'public_key': self.pem(self.key)}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
