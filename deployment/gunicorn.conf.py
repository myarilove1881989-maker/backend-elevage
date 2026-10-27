"""Candidate principal-backend production config. No migration or release hook is executed."""
import os

# Deliberately override accidental inherited settings selection for this entry point.
os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings_production"
os.environ["APP_ENV"] = "production"

bind = "0.0.0.0:" + os.environ.get("PORT", "10000")
workers = 1
worker_class = "gthread"
threads = 2
timeout = 30
graceful_timeout = 25
preload_app = False
# Keep query strings, credentials and tokens out of access logs.
accesslog = None
errorlog = "-"
capture_output = False
