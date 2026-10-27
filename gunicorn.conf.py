"""Pilot queue lifecycle; Gunicorn loads this file automatically at startup."""


def post_worker_init(worker):
    from core.reset_mail_worker import start
    start()


def worker_exit(server, worker):
    from core.reset_mail_worker import stop
    stop()
