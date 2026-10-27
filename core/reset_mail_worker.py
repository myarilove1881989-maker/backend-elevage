"""Small pilot-only queue consumer inside the existing Gunicorn worker.

Only Gunicorn's post_worker_init hook starts it, never migrations or shell.
Each SMTP delivery retains the queue's database lock and 15-second timeout.
"""
import logging
import threading

from django.conf import settings
from django.db import connections

from .reset_mail import deliver_pending

_stop = threading.Event()
_thread = None
logger = logging.getLogger(__name__)


def enabled():
    return (getattr(settings, 'APP_ENV', '') == 'pilot'
            and getattr(settings, 'PILOT_RESET_MAIL_AUTOMATIC', False))


def run_loop(stop, interval=2):
    while not stop.is_set():
        try:
            deliver_pending(limit=1)
        except Exception:
            # Never log SMTP responses, destinations, tokens, or credentials.
            logger.error('Pilot reset queue cycle failed')
        finally:
            connections.close_all()
        stop.wait(interval)


def start():
    global _thread
    if not enabled():
        return
    if is_running():
        return
    from .pilot_checks import verify_database
    verify_database()
    if (settings.EMAIL_BACKEND != 'django.core.mail.backends.smtp.EmailBackend'
            or not settings.EMAIL_HOST_PASSWORD or not 0 < settings.EMAIL_TIMEOUT <= 15):
        raise RuntimeError('Pilot reset queue requires configured SMTP with bounded timeout')
    _stop.clear()
    _thread = threading.Thread(target=run_loop, args=(_stop,), name='pilot-reset-mail', daemon=True)
    _thread.start()
    logger.info('Pilot reset queue consumer started')


def is_running():
    return _thread is not None and _thread.is_alive() and not _stop.is_set()


def stop():
    _stop.set()
    if _thread is not None:
        _thread.join(timeout=25)
