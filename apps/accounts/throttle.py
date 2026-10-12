"""Fixed-window request counters in Django's cache (shared across workers in production)."""

import hashlib
from enum import StrEnum

from django.core.cache import cache


class ThrottleScope(StrEnum):
    EMAIL_VERIFICATION = "email-verification"
    PASSWORD_RESET_EMAIL = "password-reset-email"
    PASSWORD_RESET_IP = "password-reset-ip"
    BOOKS_EXPORT = "books-export"


def allow(scope: ThrottleScope, identity: str, *, limit: int, window_seconds: int) -> bool:
    """Count one event for ``identity`` and say whether it is still within ``limit`` per window."""
    digest = hashlib.sha256(identity.encode()).hexdigest()[:32]  # no personal data in cache keys
    key = f"throttle:{scope}:{digest}"
    cache.add(key, 0, window_seconds)
    try:
        count = cache.incr(key)
    except ValueError:  # expired between add() and incr()
        cache.set(key, 1, window_seconds)
        count = 1
    return count <= limit
