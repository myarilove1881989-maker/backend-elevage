"""Isolated pilot settings. Production settings are unchanged."""
import os
import hashlib
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from django.core.exceptions import ImproperlyConfigured
from .pilot_policy import validate_environment

try:
    _web_host = validate_environment(os.environ)
except ValueError as error:
    raise ImproperlyConfigured(str(error)) from None

from .settings import *  # noqa: F403,E402

APP_ENV = 'pilot'
PILOT_RESET_MAIL_AUTOMATIC = os.environ.get('PILOT_RESET_MAIL_AUTOMATIC', 'false').lower() == 'true'
DEBUG = False
ALLOWED_HOSTS = [_web_host]
ROOT_URLCONF = 'config.pilot_urls'
CORS_ALLOW_ALL_ORIGINS = False
CORS_ALLOWED_ORIGINS = ['https://elevage-mobile.onrender.com']
CORS_ALLOW_CREDENTIALS = False
CSRF_TRUSTED_ORIGINS = ['https://' + _web_host]
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
# Stay inert until SMTP is explicitly selected in the pilot environment.
EMAIL_BACKEND = os.environ.get('EMAIL_BACKEND', 'django.core.mail.backends.dummy.EmailBackend')
DATABASES['default']['CONN_MAX_AGE'] = 60
DATABASES['default']['OPTIONS'].update(connect_timeout=10)
OFFLINE_SIGNING_KEY_ID = 'pilot-ed25519-v1'
# Render generates the seed once, independently of production. Never log it.
_seed = hashlib.sha256(b'ELEVAGE-PILOT-ED25519-V1\x00' + os.environ['PILOT_ED25519_SEED'].encode('utf-8')).digest()
_key = Ed25519PrivateKey.from_private_bytes(_seed)
OFFLINE_SIGNING_PRIVATE_KEY = _key.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption()).decode('ascii')
del _key, _seed
