"""Fail-closed pilot policy, independently testable without Django startup."""
import re
from urllib.parse import urlsplit, unquote


def validate_environment(env):
    def required(name):
        value = env.get(name, '')
        if not value:
            raise ValueError('PILOT_CONFIG: missing ' + name)
        return value

    if required('APP_ENV') != 'pilot':
        raise ValueError('PILOT_CONFIG: APP_ENV must be pilot')
    host = required('PILOT_DB_HOST')
    if not re.fullmatch(r'dpg-[a-z0-9]+-a', host):
        raise ValueError('PILOT_CONFIG: internal Render database host required')
    if required('PILOT_DB_NAME') != 'elevage_pilot' or required('PILOT_DB_USER') != 'elevage_pilot_owner':
        raise ValueError('PILOT_CONFIG: pilot database and role required')
    try:
        url = urlsplit(required('DATABASE_URL'))
        valid = (url.scheme in ('postgres', 'postgresql') and url.hostname == host
                 and url.port in (None, 5432) and url.username == 'elevage_pilot_owner'
                 and unquote(url.path) == '/elevage_pilot' and bool(url.password)
                 and not url.query and not url.fragment)
    except ValueError:
        valid = False
    if not valid:
        raise ValueError('PILOT_CONFIG: database destination mismatch')
    web_host = required('PILOT_WEB_HOST')
    if not re.fullmatch(r'backend-elevage-pilot(?:-[a-z0-9]+)?\.onrender\.com', web_host):
        raise ValueError('PILOT_CONFIG: dedicated pilot HTTPS host required')
    if env.get('DEBUG', 'False').lower() != 'false':
        raise ValueError('PILOT_CONFIG: DEBUG forbidden')
    for name in ('SECRET_KEY', 'PILOT_OWNER_PASSWORD', 'PILOT_OPERATOR_PASSWORD'):
        if len(required(name)) < 32:
            raise ValueError('PILOT_CONFIG: independent strong secret required for ' + name)
    if not re.fullmatch(r'[0-9a-fA-F]{64}', required('PILOT_ED25519_SEED')):
        raise ValueError('PILOT_CONFIG: 32-byte Ed25519 seed required')
    if env.get('OFFLINE_SIGNING_PRIVATE_KEY'):
        raise ValueError('PILOT_CONFIG: inherited signing key forbidden')
    return web_host
