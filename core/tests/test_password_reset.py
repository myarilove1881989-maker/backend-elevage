from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from core.models import PasswordResetCode
from unittest.mock import patch
from rest_framework_simplejwt.tokens import RefreshToken


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="no-reply@elevage.test",
)
class PasswordResetTests(APITestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="eleveur",
            email="eleveur@example.com",
            password="AncienMotDePasse!45",
        )

    def request_code(self):
        response = self.client.post(
            "/api/password-reset/request/",
            {"email": self.user.email},
            format="json",
        )

        from core.reset_mail import deliver_pending
        deliver_pending()
        return response

    def extract_code(self):
        body = mail.outbox[-1].body
        return body.split(" : ", 1)[1].splitlines()[0]

    def test_complete_password_reset_flow(self):
        response = self.request_code()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)

        response = self.client.post(
            "/api/password-reset/verify/",
            {"email": self.user.email, "code": self.extract_code()},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        reset_token = response.data["reset_token"]

        response = self.client.post(
            "/api/password-reset/confirm/",
            {
                "email": self.user.email,
                "reset_token": reset_token,
                "new_password": "NouveauMotDePasse!67",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)

        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("NouveauMotDePasse!67"))
        self.assertIsNotNone(PasswordResetCode.objects.get().used_at)

    def test_unknown_email_returns_generic_success(self):
        response = self.client.post(
            "/api/password-reset/request/",
            {"email": "inconnu@example.com"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 0)

    def test_expired_code_is_rejected(self):
        self.request_code()
        PasswordResetCode.objects.update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        response = self.client.post(
            "/api/password-reset/verify/",
            {"email": self.user.email, "code": self.extract_code()},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_code_is_blocked_after_five_failed_attempts(self):
        self.request_code()
        wrong_code = '111111' if self.extract_code() == '000000' else '000000'
        for _ in range(5):
            response = self.client.post(
                "/api/password-reset/verify/",
                {"email": self.user.email, "code": wrong_code},
                format="json",
            )
            self.assertEqual(response.status_code, 400)

        reset_code = PasswordResetCode.objects.get()
        self.assertEqual(reset_code.attempts, 5)
        self.assertIsNotNone(reset_code.used_at)

    def verified_token(self):
        self.request_code()
        self.code = self.extract_code()
        response = self.client.post('/api/password-reset/verify/',
            {'email': self.user.email, 'code': self.code}, format='json')
        return response.data['reset_token']

    def confirm(self, token, password='NouveauMotDePasse!67'):
        return self.client.post('/api/password-reset/confirm/', {
            'email': self.user.email, 'reset_token': token,
            'new_password': password}, format='json')

    def test_token_and_code_cannot_be_replayed_and_refresh_is_revoked(self):
        refresh = RefreshToken.for_user(self.user)
        token = self.verified_token()
        repeated = self.client.post('/api/password-reset/verify/',
            {'email': self.user.email, 'code': self.code}, format='json')
        self.assertEqual(repeated.status_code, 400)
        self.assertEqual(self.confirm(token).status_code, 200)
        self.assertEqual(self.confirm(token).status_code, 400)
        from rest_framework_simplejwt.exceptions import TokenError
        with self.assertRaises(TokenError):
            RefreshToken(str(refresh))

    def test_weak_password_does_not_consume_token(self):
        token = self.verified_token()
        self.assertEqual(self.confirm(token, '123').status_code, 400)
        self.assertEqual(self.confirm(token).status_code, 200)

    def test_tampered_expired_and_inactive_tokens_are_rejected(self):
        token = self.verified_token()
        self.assertEqual(self.confirm(token + 'x').status_code, 400)
        PasswordResetCode.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.confirm(token).status_code, 400)
        PasswordResetCode.objects.update(expires_at=timezone.now() + timedelta(minutes=1))
        self.user.is_active = False
        self.user.save()
        self.assertEqual(self.confirm(token).status_code, 400)

    @patch('core.reset_mail.send_mail', side_effect=OSError('synthetic failure'))
    def test_delivery_failure_is_neutral(self, send):
        known = self.request_code()
        unknown = self.client.post('/api/password-reset/request/',
            {'email': 'unknown@example.com'}, format='json')
        self.assertEqual(known.status_code, unknown.status_code)
        self.assertEqual(known.data, unknown.data)
        self.assertIsNotNone(PasswordResetCode.objects.get().used_at)

    def test_an_expired_login_token_does_not_block_recovery(self):
        self.client.credentials(HTTP_AUTHORIZATION='Bearer invalid')
        self.assertEqual(self.request_code().status_code, 200)

    def test_rate_limits_cover_unknown_accounts_and_ignore_forwarded_header(self):
        for i in range(31):
            response = self.client.post('/api/password-reset/verify/',
                {'email': f'unknown{i}@example.com', 'code': '123456'},
                HTTP_X_FORWARDED_FOR=f'192.0.2.{i}', format='json')
        self.assertEqual(response.status_code, 429)

    def test_resend_cooldown_and_hourly_email_limit(self):
        for _ in range(4):
            self.assertEqual(self.request_code().status_code, 200)
        self.assertEqual(len(mail.outbox), 1)

    @patch('core.reset_mail.send_mail')
    def test_public_request_queues_encrypted_mail_without_contacting_provider(self, send):
        from core.models import PasswordResetEmail
        from core.reset_mail import cipher, deliver_pending
        response = self.client.post('/api/password-reset/request/',
            {'email': self.user.email}, format='json')
        self.assertEqual(response.status_code, 200)
        send.assert_not_called()
        job = PasswordResetEmail.objects.get()
        code = cipher().decrypt(bytes(job.encrypted_code)).decode()
        self.assertEqual(len(code), 6)
        self.assertNotIn(code.encode(), bytes(job.encrypted_code))
        PasswordResetCode.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(deliver_pending(), 0)
        send.assert_not_called()
        self.assertFalse(PasswordResetEmail.objects.exists())


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class RegistrationEmailTests(APITestCase):
    def test_registration_requires_and_saves_unique_email(self):
        response = self.client.post(
            "/api/register/",
            {
                "username": "nouvel-eleveur",
                "email": "NOUVEAU@example.com",
                "password": "MotDePasseSolide!45",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        user = get_user_model().objects.get(username="nouvel-eleveur")
        self.assertEqual(user.email, "nouveau@example.com")
        self.assertIsNotNone(user.exploitation)

        duplicate = self.client.post(
            "/api/register/",
            {
                "username": "autre-eleveur",
                "email": "nouveau@example.com",
                "password": "MotDePasseSolide!89",
            },
            format="json",
        )
        self.assertEqual(duplicate.status_code, 400)
