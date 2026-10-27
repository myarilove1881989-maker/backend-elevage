"""Database configuration only: importing this module never connects to a DB."""
from copy import deepcopy

import dj_database_url
from django.core.exceptions import ImproperlyConfigured


def production_environment(environ):
    return (
        environ.get("APP_ENV", "").strip().lower() == "production"
        or environ.get("RENDER", "").strip().lower() == "true"
        or environ.get("DJANGO_SETTINGS_MODULE") == "config.settings_production"
    )


def build_databases(url, *, debug, production, base_dir):
    url = (url or "").strip()
    if production and debug:
        raise ImproperlyConfigured("DEBUG must be false in production.")
    if production and not url:
        raise ImproperlyConfigured("DATABASE_URL is required in production.")
    if url:
        try:
            default = dj_database_url.parse(
                url, conn_max_age=600, ssl_require=not production and not debug,
            )
        except (ValueError, TypeError):
            # Never interpolate the URL or propagate credentials in error text.
            raise ImproperlyConfigured("Invalid DATABASE_URL.") from None
    else:
        default = {"ENGINE": "django.db.backends.sqlite3", "NAME": base_dir / "db.sqlite3"}
    if production:
        if default["ENGINE"] != "django.db.backends.postgresql":
            raise ImproperlyConfigured("Production requires PostgreSQL; SQLite is forbidden.")
        if not all(default.get(key) for key in ("HOST", "NAME", "USER", "PASSWORD")):
            raise ImproperlyConfigured("Production requires an explicit PostgreSQL host and credentials.")
        options = default.setdefault("OPTIONS", {})
        # Preserve explicit CA/hostname verification; upgrade weaker modes to TLS.
        if options.get("sslmode") not in {"verify-ca", "verify-full"}:
            options["sslmode"] = "require"

    health = deepcopy(default)
    health.update(CONN_MAX_AGE=0, CONN_HEALTH_CHECKS=False, ATOMIC_REQUESTS=False)
    if health["ENGINE"] == "django.db.backends.postgresql":
        options = health.setdefault("OPTIONS", {})
        options["connect_timeout"] = 2
        previous = options.get("options", "")
        options["options"] = (
            previous + " -c default_transaction_read_only=on"
            " -c statement_timeout=1000 -c lock_timeout=1000"
        ).strip()
    return {"default": default, "health": health}
