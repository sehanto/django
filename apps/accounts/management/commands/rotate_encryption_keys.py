"""Re-encrypt stored 2FA secrets with the newest key in FIELD_ENCRYPTION_KEYS.

Rotation: put a new key first in FIELD_ENCRYPTION_KEYS (keep the old one after it),
deploy, run this command, then remove the old key. Backup codes are one-way hashes and
can't be re-hashed; keep the old key until users have regenerated or used up their codes.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.accounts.models import AuthenticatorDevice
from apps.core import crypto


class Command(BaseCommand):
    help = "Re-encrypt authenticator secrets with the newest FIELD_ENCRYPTION_KEYS key."

    @transaction.atomic
    def handle(self, *args, **options):
        devices = AuthenticatorDevice.objects.select_for_update()
        for device in devices:
            device.encrypted_key = crypto.rotate(device.encrypted_key)
            device.save(update_fields=["encrypted_key"])
        self.stdout.write(f"Re-encrypted {devices.count()} authenticator secret(s) with the newest key.")
