"""2FA secrets at rest: authenticator keys encrypted, backup codes as keyed hashes."""

import hashlib
import importlib
import time
from base64 import b32encode
from binascii import hexlify

import pytest
from cryptography.fernet import Fernet, InvalidToken
from django.apps import apps
from django.core.management import call_command
from django.db import connection
from django_otp.oath import TOTP
from django_otp.plugins.otp_static.models import StaticDevice, StaticToken
from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.accounts import two_factor
from apps.accounts.models import AuthenticatorDevice, BackupCode

OLD_KEY, NEW_KEY = Fernet.generate_key().decode(), Fernet.generate_key().decode()


def current_code(secret: bytes, steps_ahead=0) -> str:
    totp = TOTP(secret)
    totp.time = time.time() + steps_ahead * 30
    return f"{totp.token():06d}"


@pytest.fixture
def enrolled(user):
    device = two_factor.start_setup(user)
    secret = device.bin_key
    codes = two_factor.confirm_setup(user, device, current_code(secret))
    return {"secret": secret, "codes": codes}


def raw_column(table: str, column: str) -> list[str]:
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT {column} FROM {table}")  # noqa: S608 - fixed identifiers, test only
        return [row[0] for row in cursor.fetchall()]


def test_database_never_contains_the_authenticator_secret(enrolled):
    stored = raw_column(AuthenticatorDevice._meta.db_table, "encrypted_key")[0]
    secret = enrolled["secret"]
    for representation in (hexlify(secret).decode(), b32encode(secret).decode()):
        assert representation.lower() not in stored.lower()
    assert stored.startswith("gAAAAA")  # Fernet token


def test_backup_codes_are_stored_as_keyed_hashes(enrolled):
    stored = set(raw_column(BackupCode._meta.db_table, "code_hash"))
    for code in enrolled["codes"]:
        assert code not in stored
        assert hashlib.sha256(code.encode()).hexdigest() not in stored  # not brute-forceable without the key


def test_legacy_plaintext_tables_stay_empty(user, enrolled):
    assert not TOTPDevice.objects.exists() and not StaticToken.objects.exists()


def test_key_rotation_lifecycle(settings, user):
    settings.FIELD_ENCRYPTION_KEYS = [OLD_KEY]
    device = two_factor.start_setup(user)
    secret = device.bin_key
    codes = two_factor.confirm_setup(user, device, current_code(secret))

    settings.FIELD_ENCRYPTION_KEYS = [NEW_KEY]  # old key missing: secrets can't be read
    with pytest.raises(InvalidToken):
        AuthenticatorDevice.objects.get(pk=device.pk).bin_key

    settings.FIELD_ENCRYPTION_KEYS = [NEW_KEY, OLD_KEY]  # new key first, old key kept
    assert two_factor.verify(user, current_code(secret, steps_ahead=1)) is not None
    assert two_factor.verify(user, codes[0]) is not None  # backup codes hashed under the old key still work
    call_command("rotate_encryption_keys", verbosity=0)

    settings.FIELD_ENCRYPTION_KEYS = [NEW_KEY]  # old key retired
    assert AuthenticatorDevice.objects.get(pk=device.pk).bin_key == secret
    fresh = two_factor.generate_backup_codes(user)
    assert two_factor.verify(user, fresh[0]) is not None


def test_migration_moves_plaintext_devices(user):
    legacy_totp = TOTPDevice.objects.create(user=user, name="Authenticator", confirmed=True, drift=1, last_t=5)
    legacy_static = StaticDevice.objects.create(user=user, name="Backup codes", confirmed=True)
    StaticToken.objects.create(device=legacy_static, token="abcd2345")
    secret = legacy_totp.bin_key

    migration = importlib.import_module("apps.accounts.migrations.0008_move_two_factor_secrets_to_encrypted_storage")
    migration.forwards(apps, schema_editor=None)

    assert not TOTPDevice.objects.exists() and not StaticDevice.objects.exists()
    moved = AuthenticatorDevice.objects.get(user=user)
    assert moved.bin_key == secret and (moved.drift, moved.last_t) == (1, 5)
    assert two_factor.verify(user, "ABCD-2345") is not None  # same code, now verified against its hash


def test_production_requires_encryption_keys(monkeypatch):
    import environ

    monkeypatch.setenv("DJANGO_SECRET_KEY", "x")
    monkeypatch.setenv("XENDIT_SECRET_KEY", "xnd_" + "production_" + "NOT-A-REAL-KEY")
    monkeypatch.setenv("XENDIT_CALLBACK_TOKEN", "x")
    monkeypatch.setenv("EMAIL_URL", "smtp://localhost:25")
    monkeypatch.setenv("TURNSTILE_SITE_KEY", "x")
    monkeypatch.setenv("TURNSTILE_SECRET_KEY", "x")
    for name in ("BILLING_SELLER_NAME", "BILLING_SELLER_NPWP", "BILLING_SELLER_ADDRESS"):
        monkeypatch.setenv(name, "x")
    monkeypatch.delenv("FIELD_ENCRYPTION_KEYS", raising=False)
    monkeypatch.setattr(environ.Env, "read_env", staticmethod(lambda *args, **kwargs: None))  # ignore local .env
    import sys

    sys.modules.pop("config.settings.prod", None)
    with pytest.raises(Exception, match="FIELD_ENCRYPTION_KEYS"):
        importlib.import_module("config.settings.prod")
