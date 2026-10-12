"""Two-factor authentication: authenticator app (TOTP) plus single-use backup codes.

Built on django-otp, which provides the device models, clock-drift handling,
replay protection (a TOTP code can't be used twice) and per-device throttling
with exponential back-off after wrong codes.
"""

import math
import time
from base64 import b32encode

import segno
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from django_otp import devices_for_user, match_token, user_has_device
from django_otp.plugins.otp_static.models import StaticDevice, StaticToken
from django_otp.plugins.otp_totp.models import TOTPDevice

User = get_user_model()
PENDING_SESSION_KEY = "two_factor_pending"


def is_enabled(user) -> bool:
    return user_has_device(user, confirmed=True)


def normalize_code(code: str) -> str:
    """Accept '123 456', '123-456' or 'ABCD EFGH' as typed from a phone or paper."""
    return "".join(ch for ch in code if ch.isalnum()).lower()


# --- Setup ----------------------------------------------------------------------------


def start_setup(user) -> TOTPDevice:
    """Return the user's unconfirmed authenticator (stable while they scan it)."""
    device = TOTPDevice.objects.filter(user=user, confirmed=False).first()
    return device or TOTPDevice.objects.create(user=user, name="Authenticator", confirmed=False)


def authenticator(user) -> TOTPDevice | None:
    return TOTPDevice.objects.filter(user=user, confirmed=True).first()


def manual_entry_key(device: TOTPDevice) -> str:
    """Base32 secret in groups of four, for typing into an app instead of scanning."""
    key = b32encode(device.bin_key).decode().rstrip("=")
    return " ".join(key[i:i + 4] for i in range(0, len(key), 4))


def qr_code_data_uri(device: TOTPDevice) -> str:
    """Rendered locally: the secret must never be sent to an online QR service."""
    return segno.make(device.config_url, error="m").svg_data_uri(scale=5, border=2)


@transaction.atomic
def confirm_setup(user, device: TOTPDevice, code: str) -> list[str] | None:
    """Activate the authenticator if ``code`` matches; return fresh backup codes."""
    if device.user_id != user.pk or device.confirmed or not device.verify_token(normalize_code(code)):
        return None
    device.confirmed = True
    device.save(update_fields=["confirmed"])
    TOTPDevice.objects.filter(user=user, confirmed=False).delete()
    return generate_backup_codes(user)


@transaction.atomic
def generate_backup_codes(user) -> list[str]:
    """Replace all backup codes. Each code works once."""
    device, _ = StaticDevice.objects.get_or_create(user=user, name="Backup codes", defaults={"confirmed": True})
    device.token_set.all().delete()
    codes = [StaticToken.random_token() for _ in range(settings.TWO_FACTOR_BACKUP_CODES)]
    StaticToken.objects.bulk_create(StaticToken(device=device, token=code) for code in codes)
    return codes


def remaining_backup_codes(user) -> int:
    return StaticToken.objects.filter(device__user=user, device__confirmed=True).count()


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
