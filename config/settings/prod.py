from email.utils import parseaddr
from urllib.parse import urlsplit

from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.core.validators import validate_email

from .base import *  # noqa: F403


def _required(name: str, why: str) -> str:
    """A setting production can't run without: missing or blank stops the start."""
    value = env.str(name, default="").strip()  # noqa: F405
    if not value:
        raise ImproperlyConfigured(f"Set {name}: {why}.")
    return value


DEBUG = False
# Signs sessions, password-reset and email-confirmation links: Django's own standard
# (check --deploy, security.W009) is enforced here, plus no placeholder words.
_SECRET_KEY_PLACEHOLDER_WORDS = ("change", "insecure", "example", "secret-key", "placeholder")


def _secret_key() -> str:
    from django.core.checks.security.base import (
        SECRET_KEY_INSECURE_PREFIX,
        SECRET_KEY_MIN_LENGTH,
        SECRET_KEY_MIN_UNIQUE_CHARACTERS,
    )

    key = _required("DJANGO_SECRET_KEY", "it signs sessions and the links in emails")
    weak = (
        len(key) < SECRET_KEY_MIN_LENGTH
        or len(set(key)) < SECRET_KEY_MIN_UNIQUE_CHARACTERS
        or key.startswith(SECRET_KEY_INSECURE_PREFIX)
        or any(word in key.lower() for word in _SECRET_KEY_PLACEHOLDER_WORDS)
    )
    if weak:
        raise ImproperlyConfigured(
            f"DJANGO_SECRET_KEY must be a random value of at least {SECRET_KEY_MIN_LENGTH} characters (generate one with: "
            "python -c \"from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())\")."
        )
    return key


SECRET_KEY = _secret_key()
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = env.bool("DJANGO_SECURE_SSL_REDIRECT", default=True)  # noqa: F405
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 60 * 60 * 24 * 30
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_CONTENT_TYPE_NOSNIFF = True
CSRF_TRUSTED_ORIGINS = env.list("DJANGO_CSRF_TRUSTED_ORIGINS", default=[])  # noqa: F405
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}

# Real payments only: a live Xendit secret key (a development key would make every
# checkout a test payment that never charges anyone) and the webhook verification token.
BILLING_GATEWAY = "xendit"
XENDIT_SECRET_KEY = _required("XENDIT_SECRET_KEY", "subscriptions are paid through Xendit")
if not XENDIT_SECRET_KEY.startswith(XENDIT_LIVE_KEY_PREFIX):  # noqa: F405
    raise ImproperlyConfigured(
        f"XENDIT_SECRET_KEY must be a live key ({XENDIT_LIVE_KEY_PREFIX}...); development keys take no real payments."  # noqa: F405
    )
XENDIT_CALLBACK_TOKEN = _required("XENDIT_CALLBACK_TOKEN", "it verifies Xendit's payment webhooks")

# The seller printed on every subscription receipt (check --deploy, core.W003, also flags
# placeholder values).
BILLING_SELLER_NAME = _required("BILLING_SELLER_NAME", "it is printed on subscription receipts")
BILLING_SELLER_NPWP = _required("BILLING_SELLER_NPWP", "it is printed on subscription receipts")
BILLING_SELLER_ADDRESS = _required("BILLING_SELLER_ADDRESS", "it is printed on subscription receipts")

# Email must really go out: password resets, address confirmations, security notices and
# renewal reminders. SMTP from one URL, e.g. EMAIL_URL=smtp+tls://user:password@smtp.example.com:587
_LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")
_email = env.email_url_config(_required("EMAIL_URL", "emails (password reset, confirmations) must be delivered"))  # noqa: F405
if _email.get("EMAIL_BACKEND") != "django.core.mail.backends.smtp.EmailBackend" or not _email.get("EMAIL_HOST"):
    raise ImproperlyConfigured("EMAIL_URL must be an SMTP server (smtp://, smtp+tls:// or smtps://); other backends deliver nothing.")
if _email["EMAIL_HOST"] not in _LOCAL_HOSTS and not (_email.get("EMAIL_USE_TLS") or _email.get("EMAIL_USE_SSL")):
    raise ImproperlyConfigured("EMAIL_URL must use TLS (smtp+tls:// or smtps://) for a remote server, or the password travels in clear text.")
vars().update(_email)

DEFAULT_FROM_EMAIL = _required("DEFAULT_FROM_EMAIL", "it is the sender of every email")
_from_address = parseaddr(DEFAULT_FROM_EMAIL)[1]
try:
    validate_email(_from_address)
except ValidationError:
    raise ImproperlyConfigured("DEFAULT_FROM_EMAIL must be an address, e.g. BukuUMKM <noreply@example.com>.") from None

# Links in emails are built from SITE_URL (never the request's Host header).
SITE_URL = _required("SITE_URL", "links in emails are built from it")
_site = urlsplit(SITE_URL)
if _site.scheme != "https" or not _site.hostname or _site.hostname in _LOCAL_HOSTS:
    raise ImproperlyConfigured("SITE_URL must be the public https:// address, e.g. https://app.example.com.")
CACHES = {"default": env.cache("CACHE_URL", default="dbcache://django_cache")}  # noqa: F405 - shared by workers

# Login CAPTCHA: real Turnstile keys are required (Cloudflare dashboard -> Turnstile).
# Cloudflare's public test keys would let every visitor through, so they are refused.
def _turnstile_key(name: str) -> str:
    value = _required(name, "the login CAPTCHA needs it")
    if value.startswith(TURNSTILE_TEST_KEY_PREFIXES):  # noqa: F405
        raise ImproperlyConfigured(f"{name} is one of Cloudflare's test keys; set the key of your Turnstile widget.")
    return value


CAPTCHA_BACKEND = "turnstile"
TURNSTILE_SITE_KEY = _turnstile_key("TURNSTILE_SITE_KEY")
TURNSTILE_SECRET_KEY = _turnstile_key("TURNSTILE_SECRET_KEY")

# Database field encryption keys (2FA secrets, backup-code hashes): required, each a real
# Fernet key, never the development key derived from SECRET_KEY. Errors name the
# position, never the key.
def _field_encryption_keys() -> list[str]:
    import base64
    import binascii

    from cryptography.fernet import Fernet

    keys = [key.strip() for key in env.list("FIELD_ENCRYPTION_KEYS", default=[]) if key.strip()]  # noqa: F405
    if not keys:
        raise ImproperlyConfigured(
            "Set FIELD_ENCRYPTION_KEYS (generate one with: python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\")."
        )
    if len(set(keys)) != len(keys):
        raise ImproperlyConfigured("FIELD_ENCRYPTION_KEYS lists the same key twice.")
    for position, key in enumerate(keys, start=1):
        try:
            Fernet(key)
            raw = base64.urlsafe_b64decode(key)
        except (ValueError, binascii.Error):
            raise ImproperlyConfigured(f"FIELD_ENCRYPTION_KEYS key {position} is not a Fernet key (32 bytes, url-safe base64).") from None
        if len(set(raw)) == 1:
            raise ImproperlyConfigured(f"FIELD_ENCRYPTION_KEYS key {position} is not random (one repeated byte).")
        if key == development_field_key(SECRET_KEY):  # noqa: F405
            raise ImproperlyConfigured(f"FIELD_ENCRYPTION_KEYS key {position} is the development key derived from DJANGO_SECRET_KEY.")
    return keys


FIELD_ENCRYPTION_KEYS = _field_encryption_keys()
