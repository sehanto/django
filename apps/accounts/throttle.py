"""Fixed-window request counters in Django's cache (shared across workers in production)."""

import hashlib

from django.core.cache import cache


def allow(scope: str, identity: str, *, limit: int, window_seconds: int) -> bool:
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
