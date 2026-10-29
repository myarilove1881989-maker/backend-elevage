from datetime import timedelta
from urllib.parse import urlsplit, parse_qs
from unittest.mock import patch
from django.core import mail, signing
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase
from core.models import User, PasswordResetCode, PasswordResetLimit
from core.reset_mail import deliver_pending
from core.reset_links import decode_link

@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',PASSWORD_RESET_WEB_URL='https://elevage-mobile.onrender.com')
class ResetLinkTests(APITestCase):
    def setUp(self):
        self.user=User.objects.create_user(username='link-owner',email='link@example.test',password='PreviousPassword!827')

    def request_link(self):
        response=self.client.post('/api/password-reset/request/',{'email':self.user.email},format='json')
        self.assertEqual(response.status_code,200)
        deliver_pending()
        url=next(line for line in mail.outbox[-1].body.splitlines() if line.startswith('https://'))
        self.assertEqual(urlsplit(url).query,'')
        self.assertNotIn(self.user.email,url)
        return parse_qs(urlsplit(url).fragment.split('?',1)[1])['token'][0]

    def validate(self,token):
        return self.client.post('/api/password-reset/link/',{'link_token':token},format='json')

    def confirm(self,token):
        return self.client.post('/api/password-reset/confirm/',{'link_token':token,'new_password':'ReplacementPassword!938'},format='json')

    def test_link_open_is_non_consuming_and_completion_revokes_old_password(self):
        token=self.request_link()
        self.assertEqual(self.validate(token).status_code,200)
        self.assertEqual(self.validate(token).status_code,200)
        self.assertEqual(self.confirm(token).status_code,200)
        self.assertEqual(self.confirm(token).status_code,400)
        self.assertEqual(self.validate(token).status_code,400)
        self.assertIn(self.client.post('/api/token/',{'username':self.user.username,'password':'PreviousPassword!827'},format='json').status_code,(401,403))
        self.assertEqual(self.client.post('/api/token/',{'username':self.user.username,'password':'ReplacementPassword!938'},format='json').status_code,200)

    def test_expiry_tampering_and_inactive_accounts_share_rejection(self):
        token=self.request_link()
        self.assertEqual(self.validate(token+'x').status_code,400)
        PasswordResetCode.objects.update(expires_at=timezone.now()-timedelta(seconds=1))
        self.assertEqual(self.confirm(token).status_code,400)
        PasswordResetCode.objects.update(expires_at=timezone.now()+timedelta(minutes=1))
        self.user.is_active=False;self.user.save()
        self.assertEqual(self.validate(token).status_code,400)
        self.assertEqual(self.confirm(token).status_code,400)

    def test_signed_timestamp_expiry_and_invalid_payload(self):
        token=self.request_link()
        with patch('django.core.signing.time.time',return_value=timezone.now().timestamp()+601):
            with self.assertRaises(signing.SignatureExpired):decode_link(token)
        invalid=signing.dumps({'user_id':[],'code_id':1},salt='elevage.password-reset-link-v1')
        self.assertEqual(self.validate(invalid).status_code,400)

    def test_known_unknown_inactive_and_rate_limited_requests_are_identical(self):
        good=self.client.post('/api/password-reset/request/',{'email':self.user.email},format='json')
        unknown=self.client.post('/api/password-reset/request/',{'email':'unknown@example.test'},format='json')
        self.user.is_active=False;self.user.save()
        inactive=self.client.post('/api/password-reset/request/',{'email':self.user.email},format='json')
        for response in [unknown,inactive]:
            self.assertEqual((response.status_code,response.data),(good.status_code,good.data))
        for _ in range(32):r=self.validate('synthetic-invalid')
        self.assertEqual(r.status_code,429)
