"""Shared, database-backed limits; never store email addresses or IPs in keys."""
from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from django.utils.crypto import salted_hmac

from .models import PasswordResetLimit


def allow_reset(request, email, action):
    now = timezone.now()
    # REMOTE_ADDR only: untrusted forwarding headers must not bypass limits.
    identities = [('ip', request.META.get('REMOTE_ADDR', ''), 30),
                  ('email', email, 10 if action != 'request' else 3)]
    allowed = True
    for kind, identity, maximum in identities:
        key = salted_hmac('password-reset-limit', f'{action}:{kind}:{identity}').hexdigest()
        with transaction.atomic():
            row, _ = PasswordResetLimit.objects.get_or_create(
                key=key, defaults={'expires_at': now + timedelta(hours=1)})
            row = PasswordResetLimit.objects.select_for_update().get(pk=row.pk)
            if row.expires_at <= now:
                row.count = 0
                row.expires_at = now + timedelta(hours=1)
            row.count += 1
            allowed = allowed and row.count <= maximum
            row.save(update_fields=['count', 'expires_at'])
    return allowed
