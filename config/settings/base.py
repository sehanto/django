"""Base settings shared by every environment."""

import base64
import hashlib
from datetime import timedelta
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("DJANGO_SECRET_KEY", default="insecure-dev-key")
DEBUG = env.bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])

DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "axes",  # login rate limiting
    "django_otp",  # two-factor authentication
    "django_otp.plugins.otp_totp",
    "django_otp.plugins.otp_static",
]

LOCAL_APPS = [
    "apps.core",
    "apps.accounts",
    "apps.tenants",
    "apps.ledger",
    "apps.contacts",
    "apps.taxes",
    "apps.invoicing",
    "apps.reports",
    "apps.billing",
    "apps.dashboard",
    "apps.audit",
]

INSTALLED_APPS = DJANGO_APPS + LOCAL_APPS

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django_otp.middleware.OTPMiddleware",  # request.user.is_verified()
    "apps.accounts.middleware.UserLanguageMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.tenants.middleware.CurrentOrganizationMiddleware",
    "apps.accounts.middleware.EmailVerificationMiddleware",
    "apps.tenants.middleware.TwoFactorRequirementMiddleware",
    # Must be last: turns a locked-out login into the lockout response.
    "axes.middleware.AxesMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.template.context_processors.i18n",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.tenants.context_processors.current_organization",
                "apps.billing.context_processors.subscription_notice",
                "apps.invoicing.context_processors.invoice_kinds",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# Credentials come only from the environment / .env (see .env.example).
# Two roles (README "Database roles"): the app connects as a data-only role
# (DATABASE_URL); schema changes - migrate, createcachetable, test databases -
# use the owner (DATABASE_OWNER_URL). Without an owner URL, one role does both.
DATABASES = {"default": env.db("DATABASE_URL")}
DATABASES["default"]["CONN_MAX_AGE"] = env.int("DB_CONN_MAX_AGE", default=60)
DATABASE_OWNER = env.db("DATABASE_OWNER_URL") if env.str("DATABASE_OWNER_URL", default="") else None
DATABASE_APP_ROLE = DATABASES["default"]["USER"] if DATABASE_OWNER else None  # granted DML after each migrate

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_USER_MODEL = "accounts.User"
AUTHENTICATION_BACKENDS = [
    "axes.backends.AxesStandaloneBackend",  # must come first: refuses locked-out attempts
    "django.contrib.auth.backends.ModelBackend",
]
LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "dashboard:home"
LOGOUT_REDIRECT_URL = "accounts:login"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Indonesia. Indonesian is the default interface language; users may switch to
# English (stored on their account, see apps.accounts.middleware).
LANGUAGE_CODE = "id"
LANGUAGES = [
    ("id", "Bahasa Indonesia"),
    ("en", "English"),
]
LOCALE_PATHS = [BASE_DIR / "locale"]
TIME_ZONE = "Asia/Jakarta"
USE_I18N = True
USE_TZ = True
# Money is formatted explicitly by the |rupiah filter; global separators would also
# localize years and IDs (e.g. "2.026") inside URLs and form values.
USE_THOUSAND_SEPARATOR = False

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

MESSAGE_TAGS = {40: "danger"}

# Subscription billing (apps.billing). Plan prices exclude PPN.
BILLING_GATEWAY = env("BILLING_GATEWAY", default="fake")  # "xendit" in production
XENDIT_SECRET_KEY = env("XENDIT_SECRET_KEY", default="")
XENDIT_CALLBACK_TOKEN = env("XENDIT_CALLBACK_TOKEN", default="")
XENDIT_API_URL = env("XENDIT_API_URL", default="https://api.xendit.co")
XENDIT_LIVE_KEY_PREFIX = "xnd_production_"  # live secret keys; production refuses others (prod.py)
BILLING_CHECKOUT_HOURS = env.int("BILLING_CHECKOUT_HOURS", default=24)  # payment link lifetime
BILLING_REMINDER_DAYS = env.int("BILLING_REMINDER_DAYS", default=7)  # remind this many days before expiry
BILLING_GRACE_DAYS = env.int("BILLING_GRACE_DAYS", default=7)  # keep the plan this long after expiry
# Seller details printed on subscription receipts.
BILLING_SELLER_NAME = env("BILLING_SELLER_NAME", default="BukuUMKM")
BILLING_SELLER_NPWP = env("BILLING_SELLER_NPWP", default="")
BILLING_SELLER_ADDRESS = env("BILLING_SELLER_ADDRESS", default="")
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="BukuUMKM <noreply@bukuumkm.id>")
SITE_URL = env("SITE_URL", default="http://localhost:8000")  # absolute links in emails

# Login rate limiting (django-axes). Lock a username+IP pair, not a whole IP: many
# Indonesian mobile users share carrier-grade NAT addresses, and locking by IP alone
# would lock out unrelated people; locking by username alone would let anyone lock
# out a user from anywhere.
AXES_FAILURE_LIMIT = env.int("AXES_FAILURE_LIMIT", default=5)
AXES_COOLOFF_TIME = timedelta(minutes=env.int("AXES_COOLOFF_MINUTES", default=15))
AXES_LOCKOUT_PARAMETERS = [["username", "ip_address"]]
AXES_RESET_ON_SUCCESS = True  # a successful login clears earlier failures
AXES_LOCKOUT_TEMPLATE = "accounts/locked_out.html"  # rendered with HTTP 429
# Django's login form passes the email to authenticate() as ``username``; axes would
# otherwise look for ``email`` (USERNAME_FIELD), see no username, and lock by IP only.
AXES_USERNAME_FORM_FIELD = "username"
AXES_USERNAME_CALLABLE = "apps.accounts.client_ip.login_username"
AXES_CLIENT_IP_CALLABLE = "apps.accounts.client_ip.client_ip"
# Number of reverse proxies in front of Django that append to X-Forwarded-For (0 = none).
TRUSTED_PROXY_COUNT = env.int("TRUSTED_PROXY_COUNT", default=0)

# Password reset by email. Links are single-use and expire after an hour (Django's
# default is 3 days). Requests are throttled per address and per IP to stop email bombing.
PASSWORD_RESET_TIMEOUT = env.int("PASSWORD_RESET_TIMEOUT", default=60 * 60)
PASSWORD_RESET_EMAILS_PER_ADDRESS_PER_HOUR = 3
PASSWORD_RESET_REQUESTS_PER_IP_PER_HOUR = 10
SITE_NAME = env("SITE_NAME", default="BukuUMKM")  # shown in emails

# Shared cache (throttling, fake gateway). Use a cache shared by all workers in
# production: "dbcache://django_cache" (run createcachetable) or a Redis URL.
CACHES = {"default": env.cache("CACHE_URL", default="locmemcache://")}

# Login CAPTCHA (Cloudflare Turnstile), shown after this many failed sign-ins from one IP
# address or for one email within the window. The default keys are Cloudflare's public
# always-pass test keys (https://developers.cloudflare.com/turnstile/troubleshooting/testing/);
# production requires real ones (config/settings/prod.py).
CAPTCHA_BACKEND = env("CAPTCHA_BACKEND", default="turnstile")  # apps.accounts.captcha.CaptchaBackend
TURNSTILE_SITE_KEY = env("TURNSTILE_SITE_KEY", default="1x00000000000000000000AA")
TURNSTILE_SECRET_KEY = env("TURNSTILE_SECRET_KEY", default="1x0000000000000000000000000000000AA")
# Cloudflare's public test keys (always pass / always fail / forced challenge): refused in
# production (prod.py) and flagged by check --deploy (core.W002).
TURNSTILE_TEST_KEY_PREFIXES = ("1x0000", "2x0000", "3x0000")
LOGIN_CAPTCHA_AFTER_FAILURES = env.int("LOGIN_CAPTCHA_AFTER_FAILURES", default=2)
LOGIN_CAPTCHA_WINDOW_MINUTES = env.int("LOGIN_CAPTCHA_WINDOW_MINUTES", default=60)

# Books export (ZIP of CSVs) per organization; each one reads the whole ledger.
BOOKS_EXPORTS_PER_HOUR = env.int("BOOKS_EXPORTS_PER_HOUR", default=10)

# Retention, applied daily by manage.py prune_security_logs (deploy/cron/crontab):
# sign-in and account-security audit events and django-axes sign-in records (IP, browser)...
SECURITY_LOG_RETENTION_DAYS = env.int("SECURITY_LOG_RETENTION_DAYS", default=90)
# ...and organization/subscription/system audit events, which document changes to the books
# (lock date, members, plan, deletion) or how their data is protected (key rotation):
# 10 years, like the books (UU KUP Art. 28(11)).
ORGANIZATION_LOG_RETENTION_DAYS = env.int("ORGANIZATION_LOG_RETENTION_DAYS", default=3650)

# Email verification after sign-up: links are valid for 3 days; resends are throttled.
EMAIL_VERIFICATION_TIMEOUT = env.int("EMAIL_VERIFICATION_TIMEOUT", default=60 * 60 * 24 * 3)
EMAIL_VERIFICATION_EMAILS_PER_HOUR = 3

# Two-factor authentication (django-otp): TOTP authenticator apps plus single-use backup codes.
OTP_TOTP_ISSUER = SITE_NAME  # label shown in the authenticator app
TWO_FACTOR_PENDING_SECONDS = 5 * 60  # time to enter the code after the password
TWO_FACTOR_MAX_ATTEMPTS = 5  # wrong codes before the password must be entered again
TWO_FACTOR_BACKUP_CODES = 10
TWO_FACTOR_REMEMBER_DAYS = 30  # "don't ask again on this device"

# Encryption keys for secrets stored in the database (2FA secrets, backup-code hashes);
# Fernet keys, newest first. Separate from SECRET_KEY. Production must set them
# (generate one with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())").
# Outside production a stable development key is derived from SECRET_KEY.
FIELD_ENCRYPTION_KEYS = env.list("FIELD_ENCRYPTION_KEYS", default=[]) or [
    base64.urlsafe_b64encode(hashlib.sha256(f"dev-field-key:{SECRET_KEY}".encode()).digest()).decode()
]
