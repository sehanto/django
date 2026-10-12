"""Application-level encryption for secrets stored in the database.

* ``encrypt`` / ``decrypt``: Fernet (AES-128-CBC + HMAC-SHA256, authenticated) for
  values the server must read back, e.g. authenticator (TOTP) secrets.
* ``keyed_hash`` / ``keyed_hash_candidates``: HMAC-SHA256 with a secret "pepper"
  for values that only need comparing, e.g. backup codes. Without the key, a
  leaked database can't be brute-forced offline.

Keys come from ``FIELD_ENCRYPTION_KEYS`` (newest first) and are deliberately separate
from ``SECRET_KEY``. Older keys still decrypt and verify, so keys can be rotated:
add a new key at the front, run ``manage.py rotate_encryption_keys``, and remove
the old key once nothing depends on it.
"""

import hashlib
import hmac
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings
from django.core.signals import setting_changed
from django.dispatch import receiver

__all__ = ["InvalidToken", "decrypt", "encrypt", "keyed_hash", "keyed_hash_candidates", "rotate"]


def _keys() -> list[bytes]:
    keys = [key.encode() if isinstance(key, str) else key for key in settings.FIELD_ENCRYPTION_KEYS]
    if not keys:
        raise RuntimeError("FIELD_ENCRYPTION_KEYS is empty.")
    return keys


@lru_cache(maxsize=1)
def _fernet() -> MultiFernet:
    return MultiFernet([Fernet(key) for key in _keys()])


@lru_cache(maxsize=1)
def _hash_keys() -> tuple[bytes, ...]:
    # Domain-separated HMAC keys derived from each encryption key.
    return tuple(hashlib.sha256(b"bukuumkm/keyed-hash/v1:" + key).digest() for key in _keys())


@receiver(setting_changed)
def _reset_cache(setting, **kwargs):
    if setting == "FIELD_ENCRYPTION_KEYS":
        _fernet.cache_clear()
        _hash_keys.cache_clear()


def encrypt(data: bytes) -> str:
    return _fernet().encrypt(data).decode()


def decrypt(token: str) -> bytes:
    return _fernet().decrypt(token.encode())


def rotate(token: str) -> str:
    """Re-encrypt with the newest key (no-op for tokens that already use it)."""
    return _fernet().rotate(token.encode()).decode()


def keyed_hash(value: str) -> str:
    """Hash with the newest key (for storing)."""
    return hmac.new(_hash_keys()[0], value.encode(), hashlib.sha256).hexdigest()


def keyed_hash_candidates(value: str) -> list[str]:
    """Hashes under every configured key (for lookups that must survive rotation)."""
    return [hmac.new(key, value.encode(), hashlib.sha256).hexdigest() for key in _hash_keys()]
