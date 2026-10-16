"""Bank reconciliation rules (pure)."""

import hashlib
from datetime import date
from decimal import Decimal

from django.db import models
from django.utils.translation import gettext_lazy as _

MATCH_DAYS = 5  # a transfer shows on the statement within a few days of its entry in the books


class StatementLineStatus(models.TextChoices):
    UNMATCHED = "unmatched", _("Not reconciled")
    MATCHED = "matched", _("Reconciled")
    IGNORED = "ignored", _("Ignored")


def line_key(account_id: int, on: date, amount: Decimal, description: str, balance: Decimal | None, occurrence: int) -> str:
    """What identifies a statement line across overlapping downloads: the same line uploaded again
    (in a file that covers the same days) has the same key; ``occurrence`` tells identical lines of
    one day apart (two transfers of the same amount with the same text)."""
    text = "|".join([str(account_id), on.isoformat(), f"{amount:.2f}", " ".join(description.lower().split()),
                     "" if balance is None else f"{balance:.2f}", str(occurrence)])
    return hashlib.sha256(text.encode()).hexdigest()
