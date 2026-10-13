"""Cloudflare Turnstile on the sign-in page, required only after repeated failures.

Normal users never see it. After ``LOGIN_CAPTCHA_AFTER_FAILURES`` failed sign-ins from
one IP address or for one email within ``LOGIN_CAPTCHA_WINDOW_MINUTES``, the form shows
the widget and the password is not even checked until Cloudflare confirms the token
(https://developers.cloudflare.com/turnstile/get-started/server-side-validation/).
django-axes still locks the email+IP pair after ``AXES_FAILURE_LIMIT`` failures.
"""

import logging
from enum import StrEnum

import requests
from django.conf import settings
from django.utils.translation import get_language

from . import throttle
from .client_ip import client_ip
from .throttle import ThrottleScope

logger = logging.getLogger(__name__)

RESPONSE_FIELD = "cf-turnstile-response"  # the hidden input the widget adds to the form
SCRIPT_URL = "https://challenges.cloudflare.com/turnstile/v0/api.js"
VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
ACTION = "login"
MAX_TOKEN_LENGTH = 2048  # Cloudflare's documented maximum
TIMEOUT_SECONDS = 10
FAKE_PASSING_TOKEN = "fake-captcha-pass"  # CAPTCHA_BACKEND=fake (tests): the only accepted token


class CaptchaBackend(StrEnum):
    TURNSTILE = "turnstile"
    FAKE = "fake"


def _window_seconds() -> int:
    return settings.LOGIN_CAPTCHA_WINDOW_MINUTES * 60


def _email(value) -> str:
    return str(value or "").strip().lower()


def record_failure(request, email) -> None:
    """Called for every failed sign-in (``user_login_failed``)."""
    if request is not None and (ip := client_ip(request)):
        throttle.hit(ThrottleScope.LOGIN_FAILURE_IP, ip, window_seconds=_window_seconds())
    if email := _email(email):
        throttle.hit(ThrottleScope.LOGIN_FAILURE_EMAIL, email, window_seconds=_window_seconds())


def record_success(email) -> None:
    """A correct password clears the email's count; the IP's count keeps running (shared offices
    and attackers look the same from here)."""
    if email := _email(email):
        throttle.clear(ThrottleScope.LOGIN_FAILURE_EMAIL, email)


def is_required(request, email="") -> bool:
    threshold = settings.LOGIN_CAPTCHA_AFTER_FAILURES
    ip = client_ip(request)
    email = _email(email)
    return bool(
        (ip and throttle.count(ThrottleScope.LOGIN_FAILURE_IP, ip) >= threshold)
        or (email and throttle.count(ThrottleScope.LOGIN_FAILURE_EMAIL, email) >= threshold)
    )


def widget(request, email="") -> dict | None:
    """Template context for the widget, or None when no CAPTCHA is needed."""
    if not is_required(request, email):
        return None
    return {"site_key": settings.TURNSTILE_SITE_KEY, "action": ACTION, "language": get_language(), "script_url": SCRIPT_URL}


def verify(request) -> bool:
    """Ask Cloudflare whether the submitted token is valid (single use). Fails closed."""
    token = request.POST.get(RESPONSE_FIELD, "")
    if not token or len(token) > MAX_TOKEN_LENGTH:
        return False
    if settings.CAPTCHA_BACKEND == CaptchaBackend.FAKE:
        return token == FAKE_PASSING_TOKEN
    payload = {"secret": settings.TURNSTILE_SECRET_KEY, "response": token}
    if ip := client_ip(request):
        payload["remoteip"] = ip
    try:
        response = requests.post(VERIFY_URL, data=payload, timeout=TIMEOUT_SECONDS)
        result = response.json()
    except (requests.RequestException, ValueError):
        logger.exception("Turnstile verification failed")
        return False
    if not isinstance(result, dict) or result.get("success") is not True:
        logger.info("Turnstile rejected a token: %s", result.get("error-codes") if isinstance(result, dict) else result)
        return False
    return result.get("action") in (None, "", ACTION)  # a token made for another form is not accepted
