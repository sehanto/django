"""'Remember this device' for two-factor sign-in.

The cookie holds ``<id>:<secret>``; only ``sha256(secret)`` is stored. The cookie is
HttpOnly, Secure in production, SameSite=Lax and limited to ``/account/`` so it only
travels to the sign-in pages. A remembered device skips the code step only; the
password is always required.
"""

import hashlib
import hmac
import re
import secrets
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from .models import TrustedDevice

COOKIE_NAME = "trusted_device"
COOKIE_PATH = "/account/"


def is_offered(user) -> bool:
    """Staff accounts (Django admin access) always enter a code."""
    return not user.is_staff


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def remember(request, response, user) -> TrustedDevice:
    secret = secrets.token_urlsafe(32)
    device = TrustedDevice.objects.create(
        user=user,
        token_hash=_hash(secret),
        password_fingerprint=user.get_session_auth_hash(),
        label=describe_user_agent(request.META.get("HTTP_USER_AGENT", "")),
        expires_at=timezone.now() + timedelta(days=settings.TWO_FACTOR_REMEMBER_DAYS),
    )
    response.set_cookie(
        COOKIE_NAME, f"{device.pk}:{secret}",
        max_age=settings.TWO_FACTOR_REMEMBER_DAYS * 24 * 3600, path=COOKIE_PATH,
        httponly=True, secure=settings.SESSION_COOKIE_SECURE or request.is_secure(), samesite="Lax",
    )
    return device


def _from_cookie(request) -> tuple[int, str] | None:
    raw = request.COOKIES.get(COOKIE_NAME, "")
    device_id, _, secret = raw.partition(":")
    return (int(device_id), secret) if device_id.isdigit() and secret else None


def recognized(request, user) -> TrustedDevice | None:
    """The remembered device in this browser for ``user``, if still valid."""
    if not is_offered(user) or (parsed := _from_cookie(request)) is None:
        return None
    device_id, secret = parsed
    device = TrustedDevice.objects.filter(pk=device_id, user=user, expires_at__gt=timezone.now()).first()
    if device is None or not hmac.compare_digest(device.token_hash, _hash(secret)):
        return None
    if not hmac.compare_digest(device.password_fingerprint, user.get_session_auth_hash()):
        device.delete()  # password changed since: forget it
        return None
    device.last_used_at = timezone.now()
    device.save(update_fields=["last_used_at"])
    return device


def current_device_id(request) -> int | None:
    parsed = _from_cookie(request)
    return parsed[0] if parsed else None


def devices_for(user):
    TrustedDevice.objects.filter(user=user, expires_at__lte=timezone.now()).delete()
    return TrustedDevice.objects.filter(user=user)


def forget(user, device_id: int | None = None) -> int:
    devices = TrustedDevice.objects.filter(user=user)
    if device_id is not None:
        devices = devices.filter(pk=device_id)
    return devices.delete()[0]


_BROWSERS = [("Edg/", "Edge"), ("OPR/", "Opera"), ("SamsungBrowser", "Samsung Internet"), ("Firefox/", "Firefox"),
             ("Chrome/", "Chrome"), ("Safari/", "Safari")]
_SYSTEMS = [(r"Android", "Android"), (r"iPhone|iPad|iOS", "iOS"), (r"Windows", "Windows"), (r"Mac OS X|Macintosh", "macOS"),
            (r"CrOS", "ChromeOS"), (r"Linux", "Linux")]


def describe_user_agent(user_agent: str) -> str:
    """'Chrome on Windows', without an extra dependency; good enough to recognize a device."""
    browser = next((name for token, name in _BROWSERS if token in user_agent), "Browser")
    system = next((name for pattern, name in _SYSTEMS if re.search(pattern, user_agent)), "")
    return f"{browser} · {system}" if system else browser
