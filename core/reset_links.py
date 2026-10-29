"""Reset links carry no email address and are never consumed by opening them."""
from urllib.parse import urlsplit, urlencode
from django.conf import settings
from django.core import signing

SALT = 'elevage.password-reset-link-v1'

def link_token(reset):
    return signing.dumps({'user_id': reset.user_id, 'code_id': reset.pk}, salt=SALT)

def decode_link(token):
    data = signing.loads(token, salt=SALT, max_age=600)
    if not isinstance(data, dict) or any(type(data.get(k)) is not int or data[k] < 1 for k in ('user_id', 'code_id')):
        raise signing.BadSignature('Invalid reset payload')
    return data

def reset_url(reset):
    base = settings.PASSWORD_RESET_WEB_URL.rstrip('/')
    uri = urlsplit(base)
    local = settings.DEBUG and uri.hostname in {'127.0.0.1', 'localhost'}
    if (uri.username or uri.password or uri.query or uri.fragment or uri.path not in {'', '/'} or
            not uri.hostname or (uri.scheme != 'https' and not (local and uri.scheme == 'http'))):
        raise ValueError('Invalid public reset origin')
    return base + '/#/reset-password?' + urlencode({'token': link_token(reset)})
