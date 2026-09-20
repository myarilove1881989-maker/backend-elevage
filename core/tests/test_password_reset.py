from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from core.models import PasswordResetCode


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
        return self.client.post(
            "/api/password-reset/request/",
            {"email": self.user.email},
            format="json",
        )

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
        for _ in range(5):
            response = self.client.post(
                "/api/password-reset/verify/",
                {"email": self.user.email, "code": "000000"},
                format="json",
            )
            self.assertEqual(response.status_code, 400)

        reset_code = PasswordResetCode.objects.get()
        self.assertEqual(reset_code.attempts, 5)
        self.assertIsNotNone(reset_code.used_at)


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
