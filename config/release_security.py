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
    enabled = environ.get("OFFLINE_FINE_PERMISSIONS_ENABLED", "false").lower()
    if enabled not in {"true", "false"}:
        raise ValueError("OFFLINE_FINE_PERMISSIONS_ENABLED must be true or false.")
    if enabled == "true":
        # Explicit opt-in, bounded lifetime, and a usable signing identity are
        # prerequisites. Never generate or replace a production key here.
        try:
            hours = int(environ.get("OFFLINE_FINE_AUTHORIZATION_HOURS", "168"))
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
            key = serialization.load_pem_private_key(
                environ.get("OFFLINE_SIGNING_PRIVATE_KEY", "").encode("ascii"), password=None)
            valid = isinstance(key, Ed25519PrivateKey) and 1 <= hours <= 168
        except (ValueError, TypeError, UnicodeError):
            valid = False
        if not valid or not environ.get("OFFLINE_SIGNING_KEY_ID", "offline-v1").strip():
            raise ValueError("Offline V2 requires an Ed25519 signing identity and a 1..168 hour window.")
    return dict(ALLOWED_HOSTS=hosts, CORS_ALLOW_ALL_ORIGINS=False,
                CORS_ALLOWED_ORIGINS=origins("CORS_ALLOWED_ORIGINS"),
                CORS_ALLOW_CREDENTIALS=False, CSRF_TRUSTED_ORIGINS=origins("CSRF_TRUSTED_ORIGINS"),
                SECURE_SSL_REDIRECT=True, SESSION_COOKIE_SECURE=True, CSRF_COOKIE_SECURE=True)
