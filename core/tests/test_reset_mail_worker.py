import threading
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TransactionTestCase, override_settings
from rest_framework.test import APIClient

from core.models import PasswordResetEmail
from core import reset_mail_worker as worker


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class AutomaticResetDeliveryTests(TransactionTestCase):
    def test_public_request_is_delivered_by_consumer_without_request_thread_smtp(self):
        get_user_model().objects.create_user(username='queue-user', email='queue@example.test')
        response = APIClient().post('/api/password-reset/request/', {'email':'queue@example.test'}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(mail.outbox)
        stop = threading.Event()
        # Terminate after the first real cycle, keeping the test deterministic.
        with patch.object(stop, 'wait', side_effect=lambda interval: stop.set()):
            worker.run_loop(stop)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['queue@example.test'])
        self.assertFalse(PasswordResetEmail.objects.exists())
        with patch.object(stop, 'wait', side_effect=lambda interval: stop.set()):
            worker.run_loop(stop)
        self.assertEqual(len(mail.outbox), 1)

    def test_transient_database_failure_does_not_stop_later_queue_delivery(self):
        stop = threading.Event()
        calls = []
        def deliver(**kwargs):
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError('synthetic database outage')
            stop.set()
        with patch.object(worker, 'deliver_pending', side_effect=deliver), \
             patch.object(stop, 'wait', return_value=False):
            worker.run_loop(stop)
        self.assertEqual(len(calls), 2)

    @override_settings(APP_ENV='production', PILOT_RESET_MAIL_AUTOMATIC=True)
    def test_automatic_delivery_is_never_started_outside_pilot(self):
        with patch('threading.Thread') as thread:
            worker.start()
        thread.assert_not_called()

    @override_settings(APP_ENV='pilot', PILOT_RESET_MAIL_AUTOMATIC=True)
    def test_health_refuses_requests_when_enabled_consumer_is_not_running(self):
        from django.test import RequestFactory
        from config.pilot_urls import health
        with patch.object(worker, 'is_running', return_value=False):
            self.assertEqual(health(RequestFactory().get('/api/pilot-health/')).status_code, 503)
