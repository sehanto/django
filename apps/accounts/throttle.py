"""Fixed-window request counters in Django's cache (shared across workers in production)."""

import hashlib
from enum import StrEnum

from django.core.cache import cache


class ThrottleScope(StrEnum):
    EMAIL_VERIFICATION = "email-verification"
    PASSWORD_RESET_EMAIL = "password-reset-email"
    PASSWORD_RESET_IP = "password-reset-ip"
    BOOKS_EXPORT = "books-export"
    FILE_UPLOAD = "file-upload"
    LIST_EXPORT = "list-export"
    API = "api"
    API_AUTH_FAILURE_IP = "api-auth-failure-ip"
    LOGIN_FAILURE_IP = "login-failure-ip"
    LOGIN_FAILURE_EMAIL = "login-failure-email"
    ACCOUNT_NOTICE = "account-notice"


def _key(scope: ThrottleScope, identity: str) -> str:
    digest = hashlib.sha256(identity.encode()).hexdigest()[:32]  # no personal data in cache keys
    return f"throttle:{scope}:{digest}"


def hit(scope: ThrottleScope, identity: str, *, window_seconds: int) -> int:
    """Count one event for ``identity``; return the count in the current window."""
    key = _key(scope, identity)
    cache.add(key, 0, window_seconds)
    try:
        return cache.incr(key)
    except ValueError:  # expired between add() and incr()
        cache.set(key, 1, window_seconds)
        return 1


def count(scope: ThrottleScope, identity: str) -> int:
    return cache.get(_key(scope, identity), 0)


def clear(scope: ThrottleScope, identity: str) -> None:
    cache.delete(_key(scope, identity))


def allow(scope: ThrottleScope, identity: str, *, limit: int, window_seconds: int) -> bool:
    """Count one event for ``identity`` and say whether it is still within ``limit`` per window."""
    return hit(scope, identity, window_seconds=window_seconds) <= limit
