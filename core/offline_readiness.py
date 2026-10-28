"""Read-only startup proof. Emits booleans and a public-key fingerprint only."""
import hashlib
import json
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from django.conf import settings


def report():
    valid = False
    fingerprint = None
    try:
        key = serialization.load_pem_private_key(settings.OFFLINE_SIGNING_PRIVATE_KEY.encode('ascii'), password=None)
        if isinstance(key, Ed25519PrivateKey):
            token = jwt.encode({'aud': 'elevage-startup-check'}, key, algorithm='EdDSA')
            jwt.decode(token, key.public_key(), algorithms=['EdDSA'], audience='elevage-startup-check')
            public = key.public_key().public_bytes(serialization.Encoding.DER,
                serialization.PublicFormat.SubjectPublicKeyInfo)
            fingerprint = hashlib.sha256(public).hexdigest()
            valid = True
    except (ValueError, TypeError, UnicodeError, jwt.PyJWTError):
        pass
    print('ELEVAGE_OFFLINE_READINESS ' + json.dumps({
        'v2_issuance_enabled': settings.OFFLINE_FINE_PERMISSIONS_ENABLED,
        'authorization_hours': settings.OFFLINE_FINE_AUTHORIZATION_HOURS,
        'ed25519_roundtrip_verified': valid,
        'public_key_sha256': fingerprint,
        'credential_contract': 1,
        'v2_conflict_review': 'READ_ONLY_V2',
    }), flush=True)
