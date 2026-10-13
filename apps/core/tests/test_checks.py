"""Deploy checks (manage.py check --deploy)."""

import importlib
import sys

import environ
import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from apps.core.checks import check_billing_seller


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


PRODUCTION_ENV = {
    "DJANGO_SECRET_KEY": "x", "XENDIT_SECRET_KEY": "x", "XENDIT_CALLBACK_TOKEN": "x", "EMAIL_URL": "smtp://localhost:25",
    "TURNSTILE_SITE_KEY": "x", "TURNSTILE_SECRET_KEY": "x", "FIELD_ENCRYPTION_KEYS": "x",
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
