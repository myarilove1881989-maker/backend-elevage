"""Explicit production settings for the principal backend candidate only."""
from .settings import *  # noqa: F403

DEBUG = os.environ.get("DEBUG", "False").lower() == "true"
DATABASES = build_databases(
    os.environ.get("DATABASE_URL"), debug=DEBUG, production=True, base_dir=BASE_DIR,
)
# Exact internal readiness path; no broad bypass of HTTPS or CSRF.
SECURE_REDIRECT_EXEMPT = [r"\Aapi/health/\Z"]

# Production-only restrictions; shared/business settings and migrations stay intact.
from .release_security import production_security
from django.core.exceptions import ImproperlyConfigured
try:
    globals().update(production_security(os.environ))
except ValueError as exc:
    raise ImproperlyConfigured(str(exc)) from None
