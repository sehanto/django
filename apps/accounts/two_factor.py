"""Two-factor authentication: authenticator app (TOTP) plus single-use backup codes.

Built on django-otp's device API (clock-drift handling, replay protection, per-device
throttling with exponential back-off). Secrets are protected at rest: authenticator
keys are encrypted and backup codes are stored as keyed hashes (see ``models.py``).
"""

import math
import secrets
import time
from base64 import b32encode

import segno
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from django_otp import devices_for_user, match_token, user_has_device

from apps.core import crypto

from .models import AuthenticatorDevice, BackupCode, BackupCodeDevice

User = get_user_model()
PENDING_SESSION_KEY = "two_factor_pending"


def is_enabled(user) -> bool:
    return user_has_device(user, confirmed=True)


def normalize_code(code: str) -> str:
    """Accept '123 456', '123-456' or 'ABCD EFGH' as typed from a phone or paper."""
    return "".join(ch for ch in code if ch.isalnum()).lower()


# --- Setup ----------------------------------------------------------------------------


def start_setup(user) -> AuthenticatorDevice:
    """Return the user's unconfirmed authenticator (stable while they scan it)."""
    device = AuthenticatorDevice.objects.filter(user=user, confirmed=False).first()
    if device is None:
        device = AuthenticatorDevice.new(user, name="Authenticator", confirmed=False)
        device.save()
    return device


def authenticator(user) -> AuthenticatorDevice | None:
    return AuthenticatorDevice.objects.filter(user=user, confirmed=True).first()


def manual_entry_key(device: AuthenticatorDevice) -> str:
    """Base32 secret in groups of four, for typing into an app instead of scanning."""
    key = b32encode(device.bin_key).decode().rstrip("=")
    return " ".join(key[i:i + 4] for i in range(0, len(key), 4))


def qr_code_data_uri(device: AuthenticatorDevice) -> str:
    """Rendered locally: the secret must never be sent to an online QR service."""
    return segno.make(device.config_url, error="m").svg_data_uri(scale=5, border=2)


@transaction.atomic
def confirm_setup(user, device: AuthenticatorDevice, code: str) -> list[str] | None:
    """Activate the authenticator if ``code`` matches; return fresh backup codes."""
    if device.user_id != user.pk or device.confirmed or not device.verify_token(normalize_code(code)):
        return None
    device.confirmed = True
    device.save(update_fields=["confirmed"])
    AuthenticatorDevice.objects.filter(user=user, confirmed=False).delete()
    return generate_backup_codes(user)


def new_backup_code() -> str:
    """8 characters, 40 random bits, lowercase base32 (no 0/1/8/9 to confuse with letters)."""
    return b32encode(secrets.token_bytes(5)).decode().lower()


@transaction.atomic
def generate_backup_codes(user) -> list[str]:
    """Replace all backup codes. Each code works once; only keyed hashes are stored,
    so the plain codes exist only in this return value (shown to the user once)."""
    device, _ = BackupCodeDevice.objects.get_or_create(user=user, name="Backup codes", defaults={"confirmed": True})
    device.codes.all().delete()
    codes = [new_backup_code() for _ in range(settings.TWO_FACTOR_BACKUP_CODES)]
    BackupCode.objects.bulk_create(BackupCode(device=device, code_hash=crypto.keyed_hash(code)) for code in codes)
    return codes


def remaining_backup_codes(user) -> int:
    return BackupCode.objects.filter(device__user=user, device__confirmed=True).count()


def retry_after(devices) -> int:
    """Seconds until a code can be checked again: 0 if any device accepts codes now.

    django-otp backs off exponentially after wrong codes (1 s, 2 s, 4 s, ...). Checking
    this *before* verifying lets us say "wait" instead of calling a correct code invalid.
    """
    waits = []
    for device in devices:
        allowed, info = device.verify_is_allowed()
        if allowed:
            return 0
        if info and info.get("locked_until"):
            waits.append(math.ceil((info["locked_until"] - timezone.now()).total_seconds()))
    return max(min(waits), 1) if waits else 0


def login_devices(user) -> list:
    return list(devices_for_user(user, confirmed=True))


def verify(user, code: str):
    """The device that accepts ``code`` (authenticator or backup code), else None."""
    code = normalize_code(code)
    return match_token(user, code) if code else None


@transaction.atomic
def disable(user) -> None:
    for device in devices_for_user(user, confirmed=None):
        device.delete()


# --- Login: password accepted, waiting for the second factor ------------------------------


def begin_login(request, user, *, next_url: str) -> None:
    request.session[PENDING_SESSION_KEY] = {
        "user": user.pk, "backend": user.backend, "next": next_url, "started": time.time(), "attempts": 0,
    }


def pending_login(request) -> dict | None:
    state = request.session.get(PENDING_SESSION_KEY)
    if not state or time.time() - state["started"] > settings.TWO_FACTOR_PENDING_SECONDS:
        cancel_login(request)
        return None
    return state


def pending_user(state: dict):
    return User.objects.filter(pk=state["user"], is_active=True).first()


def record_failed_attempt(request) -> bool:
    """Count a wrong code; return False once the limit is reached (password needed again)."""
    state = request.session[PENDING_SESSION_KEY]
    state["attempts"] += 1
    request.session.modified = True
    if state["attempts"] >= settings.TWO_FACTOR_MAX_ATTEMPTS:
        cancel_login(request)
        return False
    return True


def cancel_login(request) -> None:
    request.session.pop(PENDING_SESSION_KEY, None)
