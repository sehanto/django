"""Deploy checks (manage.py check --deploy)."""

import base64
import importlib
import secrets
import sys
from unittest import mock

import environ
import pytest
from cryptography.fernet import Fernet
from django.core.exceptions import ImproperlyConfigured
from django.core.management.utils import get_random_secret_key
from django.test import override_settings

from apps.core.checks import check_billing_seller
from config.settings.base import development_field_key


@pytest.mark.parametrize(("npwp", "address", "warned"), [
    ("012345678901000", "Jl. Sudirman No. 1, Jakarta", False),
    ("0123456789012345", "Jl. Sudirman No. 1, Jakarta", False),
    ("0000000000000000", "Jl. Sudirman No. 1, Jakarta", True),  # all zeros
    ("", "Jl. Sudirman No. 1, Jakarta", True),
    ("12345", "Jl. Sudirman No. 1, Jakarta", True),
    ("0123456789012345", "Jl. Contoh No. 1, Jakarta (contoh)", True),
    ("0123456789012345", "", True),
])
def test_deploy_check_flags_placeholder_seller_details(npwp, address, warned):
    with override_settings(BILLING_SELLER_NAME="PT BukuUMKM Indonesia", BILLING_SELLER_NPWP=npwp, BILLING_SELLER_ADDRESS=address):
        assert [w.id for w in check_billing_seller(None)] == (["core.W003"] if warned else [])


LIVE_KEY = "xnd_" + "production_" + "NOT-A-REAL-KEY"
FIELD_KEY, NEXT_FIELD_KEY = Fernet.generate_key().decode(), Fernet.generate_key().decode()  # fresh per run, never committed
SECRET_KEY = get_random_secret_key()
APP_DB_PASSWORD, OWNER_DB_PASSWORD = secrets.token_urlsafe(24), secrets.token_urlsafe(24)  # fresh per run  # assembled so no key-shaped literal sits in the repo
PRODUCTION_ENV = {
    "DATABASE_URL": f"postgres://msme_app:{APP_DB_PASSWORD}@db.internal:5432/msme_db",
    "DATABASE_OWNER_URL": f"postgres://msme_user:{OWNER_DB_PASSWORD}@db.internal:5432/msme_db",
    "DJANGO_SECRET_KEY": SECRET_KEY, "XENDIT_SECRET_KEY": LIVE_KEY, "XENDIT_CALLBACK_TOKEN": "x",
    "EMAIL_URL": "smtp+tls://user:pass@smtp.example.com:587", "DEFAULT_FROM_EMAIL": "BukuUMKM <noreply@example.com>",
    "SITE_URL": "https://app.bukuumkm.id", "DJANGO_ALLOWED_HOSTS": "app.bukuumkm.id",
    "TURNSTILE_SITE_KEY": "0x4AAAAAAAsitekey", "TURNSTILE_SECRET_KEY": "0x4AAAAAAAsecret", "FIELD_ENCRYPTION_KEYS": FIELD_KEY,
    "BILLING_SELLER_NAME": "PT BukuUMKM Indonesia", "BILLING_SELLER_NPWP": "0123456789012345",
    "BILLING_SELLER_ADDRESS": "Jl. Sudirman No. 1, Jakarta",
}


def load_production_settings(monkeypatch, **overrides):
    for name, value in {**PRODUCTION_ENV, **overrides}.items():
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    monkeypatch.setattr(environ.Env, "read_env", staticmethod(lambda *args, **kwargs: None))  # ignore local .env
    # Re-read base.py too: the running test settings already loaded (and adjusted) it.
    running_base = sys.modules.pop("config.settings.base")
    sys.modules.pop("config.settings.prod", None)
    try:
        return importlib.import_module("config.settings.prod")
    finally:
        sys.modules.pop("config.settings.prod", None)
        sys.modules["config.settings.base"] = running_base


@pytest.mark.parametrize("name", ["BILLING_SELLER_NAME", "BILLING_SELLER_NPWP", "BILLING_SELLER_ADDRESS"])
@pytest.mark.parametrize("value", [None, "", "   "])  # missing, empty, blank
def test_production_requires_seller_details(monkeypatch, name, value):
    with pytest.raises(ImproperlyConfigured, match=name):
        load_production_settings(monkeypatch, **{name: value})


def test_production_starts_with_seller_details(monkeypatch):
    prod = load_production_settings(monkeypatch)
    assert prod.BILLING_SELLER_NPWP == "0123456789012345"


@pytest.mark.parametrize("name", ["TURNSTILE_SITE_KEY", "TURNSTILE_SECRET_KEY"])
@pytest.mark.parametrize("value", [None, "", "1x00000000000000000000AA", "2x0000000000000000000000000000000AA", "3x00000000000000000000FF"])
def test_production_requires_real_turnstile_keys(monkeypatch, name, value):
    with pytest.raises(ImproperlyConfigured, match=name):
        load_production_settings(monkeypatch, **{name: value})


@pytest.mark.parametrize(("name", "value"), [
    ("XENDIT_SECRET_KEY", None), ("XENDIT_SECRET_KEY", ""), ("XENDIT_SECRET_KEY", "xnd_development_NOT-A-REAL-KEY"),
    ("XENDIT_SECRET_KEY", "x"), ("XENDIT_CALLBACK_TOKEN", None), ("XENDIT_CALLBACK_TOKEN", "  "),
])
def test_production_requires_live_xendit_keys(monkeypatch, name, value):
    with pytest.raises(ImproperlyConfigured, match=name):
        load_production_settings(monkeypatch, **{name: value})


@pytest.mark.parametrize(("name", "value"), [
    ("EMAIL_URL", None), ("EMAIL_URL", ""), ("EMAIL_URL", "consolemail://"), ("EMAIL_URL", "dummymail://"),
    ("EMAIL_URL", "filemail:///tmp/mail"), ("EMAIL_URL", "memorymail://"),
    ("EMAIL_URL", "smtp://user:pass@smtp.example.com:25"),  # remote without TLS: password in clear text
    ("DEFAULT_FROM_EMAIL", None), ("DEFAULT_FROM_EMAIL", " "), ("DEFAULT_FROM_EMAIL", "BukuUMKM"),
    ("SITE_URL", None), ("SITE_URL", "http://app.example.com"), ("SITE_URL", "https://localhost:8000"),
    ("SITE_URL", "https://127.0.0.1"),
])
def test_production_requires_real_email_settings(monkeypatch, name, value):
    with pytest.raises(ImproperlyConfigured, match=name):
        load_production_settings(monkeypatch, **{name: value})


@pytest.mark.parametrize("url", ["smtps://user:pass@smtp.example.com:465", "smtp://localhost:25"])
def test_production_accepts_encrypted_or_local_smtp(monkeypatch, url):
    prod = load_production_settings(monkeypatch, EMAIL_URL=url)
    assert prod.EMAIL_BACKEND == "django.core.mail.backends.smtp.EmailBackend"


ZERO_KEY = base64.urlsafe_b64encode(bytes(32)).decode()


@pytest.mark.parametrize("value", [
    None, "", " , ", "x", "not-base64-at-all!", ZERO_KEY,
    f"{FIELD_KEY},{FIELD_KEY}",  # listed twice
    "DEV",  # the development key derived from DJANGO_SECRET_KEY
])
def test_production_requires_real_encryption_keys(monkeypatch, value):
    if value == "DEV":
        value = development_field_key(PRODUCTION_ENV["DJANGO_SECRET_KEY"])
    with pytest.raises(ImproperlyConfigured, match="FIELD_ENCRYPTION_KEYS"):
        load_production_settings(monkeypatch, FIELD_ENCRYPTION_KEYS=value)


def test_production_accepts_rotated_keys(monkeypatch):
    prod = load_production_settings(monkeypatch, FIELD_ENCRYPTION_KEYS=f"{NEXT_FIELD_KEY},{FIELD_KEY}")
    assert prod.FIELD_ENCRYPTION_KEYS == [NEXT_FIELD_KEY, FIELD_KEY]


@pytest.mark.parametrize("value", [
    None, "", "x", "change-me", "insecure-dev-key",
    "a" * 60,  # long but one character
    "django-insecure-" + "k3j4h5g6f7d8s9a0q1w2e3r4t5y6u7i8o9p0z1x2c3v4b5n6m7",  # Django's startproject marker
    "please-change-this-to-a-long-random-value-0123456789abcdef",  # long, but a placeholder
])
def test_production_requires_a_real_secret_key(monkeypatch, value):
    with pytest.raises(ImproperlyConfigured, match="DJANGO_SECRET_KEY"):
        load_production_settings(monkeypatch, DJANGO_SECRET_KEY=value)


@pytest.mark.parametrize(("name", "url"), [
    ("DATABASE_URL", "postgres://msme_app@db.internal:5432/msme_db"),  # no password
    ("DATABASE_URL", "postgres://msme_app:short1@db.internal:5432/msme_db"),
    ("DATABASE_URL", "postgres://msme_app:CHANGE_ME_CHANGE_ME_2026@db.internal:5432/msme_db"),  # .env.example placeholder
    ("DATABASE_URL", "postgres://msme_app:aaaaaaaaaaaaaaaaaaaaaaaa@db.internal:5432/msme_db"),  # repetitive
    ("DATABASE_URL", "postgres://a_long_user_name_x:a_long_user_name_x@db.internal:5432/msme_db"),  # = user
    ("DATABASE_OWNER_URL", "postgres://msme_user:postgres-postgres-2026@db.internal:5432/msme_db"),
])
def test_production_requires_real_database_passwords(monkeypatch, name, url):
    with pytest.raises(ImproperlyConfigured, match=name) as refused:
        load_production_settings(monkeypatch, **{name: url})
    assert url.split("@")[0].split(":")[-1] not in str(refused.value)  # the password is never echoed


def test_production_without_an_owner_url_still_checks_the_app_url(monkeypatch):
    prod = load_production_settings(monkeypatch, DATABASE_OWNER_URL=None)
    assert prod.DATABASE_OWNER is None and prod.DATABASES["default"]["PASSWORD"] == APP_DB_PASSWORD


@pytest.mark.parametrize(("overrides", "message"), [
    ({"DJANGO_ALLOWED_HOSTS": None}, "DJANGO_ALLOWED_HOSTS"),
    ({"DJANGO_ALLOWED_HOSTS": " , "}, "DJANGO_ALLOWED_HOSTS"),
    ({"DJANGO_ALLOWED_HOSTS": "*"}, "catch-all"),
    ({"DJANGO_ALLOWED_HOSTS": "app.bukuumkm.id,localhost"}, "localhost"),
    ({"DJANGO_ALLOWED_HOSTS": "127.0.0.1"}, "127.0.0.1"),
    ({"DJANGO_ALLOWED_HOSTS": "app.example.com", "SITE_URL": "https://app.example.com"}, "example"),
    ({"DJANGO_ALLOWED_HOSTS": "shop.test"}, "shop.test"),
    ({"DJANGO_ALLOWED_HOSTS": "other.bukuumkm.id"}, "SITE_URL"),  # email links would be rejected
])
def test_production_requires_real_allowed_hosts(monkeypatch, overrides, message):
    with pytest.raises(ImproperlyConfigured, match=message):
        load_production_settings(monkeypatch, **overrides)


@pytest.mark.parametrize("hosts", ["app.bukuumkm.id", ".bukuumkm.id", "bukuumkm.id,app.bukuumkm.id", "APP.BUKUUMKM.ID"])
def test_production_accepts_public_hosts(monkeypatch, hosts):
    prod = load_production_settings(monkeypatch, DJANGO_ALLOWED_HOSTS=hosts, DJANGO_CSRF_TRUSTED_ORIGINS="https://app.bukuumkm.id")
    assert prod.ALLOWED_HOSTS


@pytest.mark.parametrize("url", ["locmemcache://", "dummycache://", "filecache:///var/tmp/cache", "memcache://mc.internal:11211"])
def test_production_requires_a_shared_cache(monkeypatch, url):
    with pytest.raises(ImproperlyConfigured, match="CACHE_URL"):
        load_production_settings(monkeypatch, CACHE_URL=url)


@pytest.mark.parametrize(("url", "backend"), [
    (None, "django.core.cache.backends.db.DatabaseCache"),  # the default
    ("rediss://:secret@redis.internal:6380/0", "django.core.cache.backends.redis.RedisCache"),
])
def test_production_accepts_shared_caches(monkeypatch, url, backend):
    assert load_production_settings(monkeypatch, CACHE_URL=url).CACHES["default"]["BACKEND"] == backend


def test_deploy_check_tests_that_the_cache_answers():
    from apps.core.checks import check_cache_answers

    assert check_cache_answers(None) == []  # the test cache works
    with override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.dummy.DummyCache"}}):
        assert [w.id for w in check_cache_answers(None)] == ["core.W004"]
    with mock.patch("django.core.cache.cache.set", side_effect=ConnectionError("redis.internal:6379 refused")):
        warnings = check_cache_answers(None)
    assert [w.id for w in warnings] == ["core.W004"] and "refused" in warnings[0].msg


@pytest.mark.parametrize("origin", [
    "http://app.bukuumkm.id",  # not https
    "https://app.bukuumkm.id/login",  # not a bare origin
    "https://user@app.bukuumkm.id",
    "https://*",
    "https://localhost:8000",
    "https://app.example.com",
    "https://evil.example.net",
    "https://partner.co.id",  # a site this app doesn't serve
    "https://*.bukuumkm.id",  # wider than the single allowed host
])
def test_production_requires_real_csrf_trusted_origins(monkeypatch, origin):
    with pytest.raises(ImproperlyConfigured, match="DJANGO_CSRF_TRUSTED_ORIGINS"):
        load_production_settings(monkeypatch, DJANGO_CSRF_TRUSTED_ORIGINS=origin)


@pytest.mark.parametrize(("hosts", "origins"), [
    ("app.bukuumkm.id", None),  # optional: the site's own forms work without it
    ("app.bukuumkm.id", "https://app.bukuumkm.id"),
    ("app.bukuumkm.id", "https://app.bukuumkm.id:8443"),
    (".bukuumkm.id", "https://*.bukuumkm.id,https://bukuumkm.id"),
])
def test_production_accepts_trusted_origins_of_served_hosts(monkeypatch, hosts, origins):
    prod = load_production_settings(monkeypatch, DJANGO_ALLOWED_HOSTS=hosts, DJANGO_CSRF_TRUSTED_ORIGINS=origins)
    assert prod.CSRF_TRUSTED_ORIGINS == (origins.split(",") if origins else [])


@pytest.mark.parametrize("value", ["false", "0", "off", "no", "ture", "enabled", ""])
def test_production_always_redirects_to_https(monkeypatch, value):
    with pytest.raises(ImproperlyConfigured, match="DJANGO_SECURE_SSL_REDIRECT"):
        load_production_settings(monkeypatch, DJANGO_SECURE_SSL_REDIRECT=value)


@pytest.mark.parametrize("value", [None, "true", "True", "1", "yes", "on"])
def test_production_redirect_accepts_true_values(monkeypatch, value):
    prod = load_production_settings(monkeypatch, DJANGO_SECURE_SSL_REDIRECT=value)
    assert prod.SECURE_SSL_REDIRECT is True and prod.SECURE_PROXY_SSL_HEADER == ("HTTP_X_FORWARDED_PROTO", "https")


ONE_YEAR = 365 * 24 * 60 * 60


@pytest.mark.parametrize(("name", "value"), [
    ("DJANGO_SECURE_HSTS_SECONDS", "0"),  # HSTS off
    ("DJANGO_SECURE_HSTS_SECONDS", str(30 * 24 * 60 * 60)),  # 30 days: below one year
    ("DJANGO_SECURE_HSTS_SECONDS", "1y"),
    ("DJANGO_SECURE_HSTS_SECONDS", "-31536000"),
    ("DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS", "false"),
    ("DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS", "ture"),  # a typo is an error, not False
    ("DJANGO_SECURE_HSTS_PRELOAD", "maybe"),
])
def test_production_requires_real_hsts(monkeypatch, name, value):
    with pytest.raises(ImproperlyConfigured, match=name):
        load_production_settings(monkeypatch, **{name: value})


@pytest.mark.parametrize(("overrides", "seconds", "preload"), [
    ({}, ONE_YEAR, False),
    ({"DJANGO_SECURE_HSTS_SECONDS": str(2 * ONE_YEAR), "DJANGO_SECURE_HSTS_PRELOAD": "true"}, 2 * ONE_YEAR, True),
    ({"DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS": "1", "DJANGO_SECURE_HSTS_PRELOAD": "false"}, ONE_YEAR, False),
])
def test_production_hsts_settings(monkeypatch, overrides, seconds, preload):
    prod = load_production_settings(monkeypatch, **overrides)
    assert (prod.SECURE_HSTS_SECONDS, prod.SECURE_HSTS_INCLUDE_SUBDOMAINS, prod.SECURE_HSTS_PRELOAD) == (seconds, True, preload)
