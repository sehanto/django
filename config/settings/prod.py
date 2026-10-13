from .base import *  # noqa: F403

DEBUG = False
SECRET_KEY = env("DJANGO_SECRET_KEY")  # noqa: F405 - fail fast if missing
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

# Real payments only: fail fast if Xendit is not configured.
BILLING_GATEWAY = "xendit"
XENDIT_SECRET_KEY = env("XENDIT_SECRET_KEY")  # noqa: F405
XENDIT_CALLBACK_TOKEN = env("XENDIT_CALLBACK_TOKEN")  # noqa: F405
# The seller printed on every subscription receipt: required and non-blank in production
# (check --deploy, core.W003, also flags placeholder values).
def _required(name: str) -> str:
    value = env.str(name, default="").strip()  # noqa: F405
    if not value:
        from django.core.exceptions import ImproperlyConfigured

        raise ImproperlyConfigured(f"Set {name}: it is printed on subscription receipts.")
    return value


BILLING_SELLER_NAME = _required("BILLING_SELLER_NAME")
BILLING_SELLER_NPWP = _required("BILLING_SELLER_NPWP")
BILLING_SELLER_ADDRESS = _required("BILLING_SELLER_ADDRESS")

# SMTP from one URL, e.g. EMAIL_URL=smtp+tls://user:password@smtp.example.com:587
vars().update(env.email_url("EMAIL_URL"))  # noqa: F405 - required in production
CACHES = {"default": env.cache("CACHE_URL", default="dbcache://django_cache")}  # noqa: F405 - shared by workers

# Login CAPTCHA: real Turnstile keys are required (Cloudflare dashboard -> Turnstile).
# Cloudflare's public test keys would let every visitor through, so they are refused.
def _turnstile_key(name: str) -> str:
    value = _required(name)
    if value.startswith(TURNSTILE_TEST_KEY_PREFIXES):  # noqa: F405
        from django.core.exceptions import ImproperlyConfigured

        raise ImproperlyConfigured(f"{name} is one of Cloudflare's test keys; set the key of your Turnstile widget.")
    return value


CAPTCHA_BACKEND = "turnstile"
TURNSTILE_SITE_KEY = _turnstile_key("TURNSTILE_SITE_KEY")
TURNSTILE_SECRET_KEY = _turnstile_key("TURNSTILE_SECRET_KEY")

# Database field encryption keys are required in production (never derived from SECRET_KEY).
FIELD_ENCRYPTION_KEYS = env.list("FIELD_ENCRYPTION_KEYS")  # noqa: F405
