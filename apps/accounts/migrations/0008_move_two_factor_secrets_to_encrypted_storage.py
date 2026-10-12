"""Move existing 2FA devices from django-otp's plaintext tables to encrypted storage.

Authenticator secrets are re-stored Fernet-encrypted; backup codes as keyed hashes.
The plaintext rows are then deleted. Users keep using the same app and codes.
"""

from binascii import unhexlify

from django.db import migrations

DEVICE_FIELDS = ["user_id", "name", "confirmed", "throttling_failure_timestamp", "throttling_failure_count",
                 "created_at", "last_used_at"]


def forwards(apps, schema_editor):
    from apps.core import crypto  # runtime helper; the key comes from settings

    PlainTOTP = apps.get_model("otp_totp", "TOTPDevice")
    PlainStatic = apps.get_model("otp_static", "StaticDevice")
    AuthenticatorDevice = apps.get_model("accounts", "AuthenticatorDevice")
    BackupCodeDevice = apps.get_model("accounts", "BackupCodeDevice")
    BackupCode = apps.get_model("accounts", "BackupCode")

    for old in PlainTOTP.objects.all():
        AuthenticatorDevice.objects.create(
            encrypted_key=crypto.encrypt(unhexlify(old.key.encode())),
            step=old.step, t0=old.t0, digits=old.digits, tolerance=old.tolerance, drift=old.drift, last_t=old.last_t,
            **{field: getattr(old, field) for field in DEVICE_FIELDS},
        )
    for old in PlainStatic.objects.all():
        device = BackupCodeDevice.objects.create(**{field: getattr(old, field) for field in DEVICE_FIELDS})
        BackupCode.objects.bulk_create(
            BackupCode(device=device, code_hash=crypto.keyed_hash(token.token)) for token in old.token_set.all()
        )
    PlainTOTP.objects.all().delete()
    PlainStatic.objects.all().delete()  # tokens cascade


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0007_encrypted_two_factor_devices"),
        ("otp_totp", "0003_add_timestamps"),
        ("otp_static", "0003_add_timestamps"),
    ]

    # Not reversible: plaintext secrets are not recreated.
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
