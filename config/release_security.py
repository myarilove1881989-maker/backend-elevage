"""Pure production security configuration. No network, files or Django imports."""
import re
from urllib.parse import urlsplit


def production_security(environ):
    hosts = [v.strip() for v in environ.get("ALLOWED_HOSTS", "backend-elevage.onrender.com").split(",") if v.strip()]
    if not hosts or any(not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", h) or ".." in h for h in hosts):
        raise ValueError("Explicit production hostnames are required; no wildcard, scheme or port.")
    def origins(key):
        values = [v.strip() for v in environ.get(key, "").split(",") if v.strip()]
        for value in values:
            parsed = urlsplit(value)
            if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                    or parsed.path or parsed.query or parsed.fragment or "*" in value):
                raise ValueError("Production origins must be explicit HTTPS origins without paths.")
            try:
                parsed.port
            except ValueError:
                raise ValueError("Invalid production origin port.") from None
        return values
    if environ.get("OFFLINE_FINE_PERMISSIONS_ENABLED", "false").lower() != "false":
        raise ValueError("This release requires fine permissions to remain disabled.")
    return dict(ALLOWED_HOSTS=hosts, CORS_ALLOW_ALL_ORIGINS=False,
                CORS_ALLOWED_ORIGINS=origins("CORS_ALLOWED_ORIGINS"),
                CORS_ALLOW_CREDENTIALS=False, CSRF_TRUSTED_ORIGINS=origins("CSRF_TRUSTED_ORIGINS"),
                SECURE_SSL_REDIRECT=True, SESSION_COOKIE_SECURE=True, CSRF_COOKIE_SECURE=True)
