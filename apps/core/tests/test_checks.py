"""Deploy checks (manage.py check --deploy)."""

import base64
import importlib
import sys

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
SECRET_KEY = get_random_secret_key()  # assembled so no key-shaped literal sits in the repo
PRODUCTION_ENV = {
    "DJANGO_SECRET_KEY": SECRET_KEY, "XENDIT_SECRET_KEY": LIVE_KEY, "XENDIT_CALLBACK_TOKEN": "x",
    "EMAIL_URL": "smtp+tls://user:pass@smtp.example.com:587", "DEFAULT_FROM_EMAIL": "BukuUMKM <noreply@example.com>",
    "SITE_URL": "https://app.example.com",
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
    sys.modules.pop("config.settings.prod", None)
    try:
        return importlib.import_module("config.settings.prod")
    finally:
        sys.modules.pop("config.settings.prod", None)


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
