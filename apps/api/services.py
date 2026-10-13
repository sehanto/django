"""API keys: creation, revocation and authentication."""

import secrets

from django.utils import timezone
from django.utils.translation import gettext as _

from apps.core.crypto import keyed_hash, keyed_hash_candidates
from apps.core.exceptions import ValidationFailed
from apps.core.sanitizers import sanitize_text
from apps.tenants.models import Membership

from .models import ApiKey

KEY_PREFIX = "eas_"


def create_key(*, organization, user, name: str) -> tuple[ApiKey, str]:
    """A new key; the secret is returned once and never stored."""
    name = sanitize_text(name).strip()[:100]
    if not name:
        raise ValidationFailed(_("Give the key a name."))
    secret = KEY_PREFIX + secrets.token_urlsafe(32)
    key = ApiKey.objects.create(organization=organization, user=user, name=name, prefix=secret[:12], key_hash=keyed_hash(secret))
    return key, secret


def revoke_key(*, organization, key_id: int) -> ApiKey:
    key = ApiKey.objects.get(organization=organization, pk=key_id)
    if key.revoked_at is None:
        key.revoked_at = timezone.now()
        key.save(update_fields=["revoked_at", "updated_at"])
    return key


def authenticate(secret: str) -> tuple[ApiKey, Membership] | None:
    """The active key and its creator's membership, or None."""
    if not secret.startswith(KEY_PREFIX) or len(secret) > 100:
        return None
    key = (
        ApiKey.objects.select_related("organization", "user")
        .filter(key_hash__in=keyed_hash_candidates(secret), revoked_at=None).first()
    )
    if key is None or not key.user.is_active:
        return None
    membership = Membership.objects.filter(organization=key.organization, user=key.user).first()
    if membership is None:
        return None
    ApiKey.objects.filter(pk=key.pk).update(last_used_at=timezone.now())
    return key, membership
