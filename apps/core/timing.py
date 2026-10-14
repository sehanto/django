"""Responses whose duration doesn't depend on secret facts (e.g. whether an account exists)."""

import time
from contextlib import contextmanager


@contextmanager
def minimum_duration(seconds: float):
    """Take at least ``seconds``: the work inside may be faster or slower depending on what
    it found, and the caller shouldn't be able to time that. Choose a floor above the slowest
    branch."""
    started = time.monotonic()
    try:
        yield
    finally:
        remaining = seconds - (time.monotonic() - started)
        if remaining > 0:
            time.sleep(remaining)
