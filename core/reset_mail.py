"""Durable encrypted mail queue. SMTP runs outside the public request path."""
import base64
import hashlib
import logging

from cryptography.fernet import Fernet
from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone

from .models import PasswordResetEmail
from .reset_links import reset_url


def cipher():
    # Domain-separated encryption key; never return it or the ciphertext to clients.
    key = hashlib.sha256(('reset-mail-v1:' + settings.SECRET_KEY).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def deliver_pending(limit=100):
    sent = 0
    for pk in list(PasswordResetEmail.objects.order_by('pk').values_list('pk', flat=True)[:limit]):
        with transaction.atomic():
            job = PasswordResetEmail.objects.select_for_update().filter(pk=pk).first()
            if job is None:
                continue
            reset = job.code
            if not reset.is_valid or not reset.user.is_active:
                job.delete()
                continue
            try:
                code = cipher().decrypt(bytes(job.encrypted_code)).decode('ascii')
                send_mail(
                    subject='Réinitialisation de votre mot de passe Élev’Age',
                    message=(f'Votre code de réinitialisation est : {code}\n\n'
                             f'Ouvrez ce lien pour choisir votre nouveau mot de passe :\n{reset_url(reset)}\n\n'
                             'Ce code expire 10 minutes après votre demande. '
                             "Si vous n’êtes pas à l’origine de cette demande, ignorez cet e-mail."),
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[reset.user.email], fail_silently=False)
                sent += 1
            except Exception:
                # Fixed message only: never log provider responses or secrets.
                logging.getLogger(__name__).error('Password reset email delivery failed')
                reset.used_at = timezone.now()
                reset.save(update_fields=['used_at'])
            job.delete()
    return sent
