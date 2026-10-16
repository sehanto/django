"""Uploads waiting for confirmation, and the record of applied ones.

Between the check and the confirmation the file waits in the shared cache, encrypted
(``apps.core.crypto``) and bound to the organization and the user who uploaded it, for an hour.
Applying it records an ``Upload`` with the file's SHA-256: the same file is never applied twice.
"""

import hashlib
import json
import secrets
from base64 import b64decode, b64encode
from dataclasses import dataclass

from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.utils.translation import gettext as _

from apps.audit.domain import AuditAction
from apps.audit.services import record as record_event
from apps.core.crypto import InvalidToken, decrypt, encrypt
from apps.core.exceptions import ValidationFailed

from .domain import Report, RowOutcome
from .models import Upload

PENDING_SECONDS = 3600
SUMMARY_LENGTH = 500


@dataclass(frozen=True)
class PendingFile:
    kind: str
    name: str
    content: bytes
    options: dict

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content).hexdigest()


def _key(organization, user, token: str) -> str:
    return f"uploads:{organization.pk}:{user.pk}:{token}"


def stash_file(*, organization, user, pending: PendingFile) -> str:
    """Keep a checked file until it is confirmed; returns the token that names it."""
    token = secrets.token_urlsafe(24)
    payload = {"kind": pending.kind, "name": pending.name, "content": b64encode(pending.content).decode(), "options": pending.options}
    cache.set(_key(organization, user, token), encrypt(json.dumps(payload).encode()), PENDING_SECONDS)
    return token


def stashed_file(*, organization, user, token: str) -> PendingFile | None:
    if not token or len(token) > 64:
        return None
    stored = cache.get(_key(organization, user, token))
    if stored is None:
        return None
    try:
        payload = json.loads(decrypt(stored))
    except (InvalidToken, ValueError):
        return None
    return PendingFile(payload["kind"], payload["name"], b64decode(payload["content"]), payload["options"])


def discard_file(*, organization, user, token: str) -> None:
    cache.delete(_key(organization, user, token))


def ensure_not_applied(organization, kind: str, sha256: str) -> None:
    earlier = Upload.objects.for_organization(organization).filter(kind=kind, sha256=sha256).first()
    if earlier is not None:
        raise ValidationFailed(
            _("This file was already applied on %(date)s.") % {"date": f"{earlier.created_at:%d-%m-%Y %H:%M}"}
        )


def summary_of(report: Report) -> str:
    parts = [
        f"{label}: {report.count(outcome)}" for outcome, label in RowOutcome.choices
        if outcome != RowOutcome.ERROR and report.count(outcome)
    ]
    return "; ".join(parts)[:SUMMARY_LENGTH]


@transaction.atomic
def record_upload(*, organization, pending: PendingFile, report: Report, created_by=None, request=None) -> Upload:
    """Record an applied file (in the transaction that applied its rows) and its audit event."""
    ensure_not_applied(organization, pending.kind, pending.sha256)
    try:
        with transaction.atomic():
            upload = Upload.objects.create(
                organization=organization, kind=pending.kind, file_name=pending.name[:255], sha256=pending.sha256,
                rows=len(report.results), applied=report.applied, skipped=report.count(RowOutcome.SKIPPED),
                summary=summary_of(report), created_by=created_by,
            )
    except IntegrityError:  # applied at the same moment from another tab
        raise ValidationFailed(_("This file was just applied.")) from None
    record_event(
        AuditAction.FILE_UPLOADED, request=request, actor=created_by, organization=organization, target=upload.file_name,
        kind=pending.kind, rows=upload.rows, applied=upload.applied,
    )
    return upload
