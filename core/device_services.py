"""Device proof: Ed25519 or Android Keystore P-256/SHA-256, never UUID alone."""
import base64
import hashlib
import uuid
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError
from .models import DeviceChallenge, DeviceRegistration


def load_public_key(pem):
    try:
        key = serialization.load_pem_public_key(pem.encode('ascii'))
        if not (isinstance(key, Ed25519PublicKey) or (
            isinstance(key, ec.EllipticCurvePublicKey) and isinstance(key.curve, ec.SECP256R1)
        )):
            raise ValueError()
        return key
    except (ValueError, TypeError, UnicodeError):
        raise ValidationError({'public_key': 'An Ed25519 or P-256 PEM public key is required.'})


def verify_signature(key, signature, message):
    if isinstance(key, Ed25519PublicKey):
        key.verify(signature, message)
    else:
        # Android SHA256withECDSA produces ASN.1 DER, not JOSE raw r||s.
        key.verify(signature, message, ec.ECDSA(hashes.SHA256()))


def request_message(request, challenge):
    digest = hashlib.sha256(request.body).hexdigest()
    return (f'ELEVAGE-DEVICE-V1\n{challenge.pk}\n{challenge.device_id}\n'
            f'{challenge.user_id}\n{challenge.purpose}\n{request.method}\n'
            f'{request.get_full_path()}\n{digest}').encode('utf-8')


@transaction.atomic
def verify_request_device(request, *, primary=False, purpose='WRITE', allow_pending=False):
    try:
        device_id = int(request.headers.get('X-Elevage-Device', ''))
        challenge_id = uuid.UUID(request.headers.get('X-Elevage-Challenge', ''))
        signature = base64.b64decode(request.headers.get('X-Elevage-Signature', ''), validate=True)
    except (ValueError, TypeError):
        raise PermissionDenied('DEVICE_PROOF_REQUIRED')
    try:
        challenge = DeviceChallenge.objects.select_for_update().select_related('device').get(
            pk=challenge_id, device_id=device_id, user=request.user, purpose=purpose,
            device__exploitation_id=request.user.exploitation_id,
        )
    except DeviceChallenge.DoesNotExist:
        raise PermissionDenied('DEVICE_PROOF_INVALID')
    device = challenge.device
    if challenge.consumed_at or challenge.expires_at <= timezone.now():
        raise PermissionDenied('DEVICE_CHALLENGE_EXPIRED_OR_USED')
    if device.status == 'REVOKED' or (not allow_pending and device.status != 'ACTIVE'):
        raise PermissionDenied('DEVICE_INACTIVE')
    if primary and (not device.is_primary_writer or device.write_generation != request.user.exploitation.write_generation):
        raise PermissionDenied('PRIMARY_DEVICE_REQUIRED')
    try:
        verify_signature(load_public_key(device.public_key), signature, request_message(request, challenge))
    except (InvalidSignature, ValueError):
        raise PermissionDenied('DEVICE_PROOF_INVALID')
    challenge.consumed_at = timezone.now()
    challenge.save(update_fields=['consumed_at'])
    # Operational heartbeat, deliberately outside the business audit.
    DeviceRegistration._base_manager.filter(pk=device.pk).update(last_seen_at=timezone.now())
    return device
