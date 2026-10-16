"""Bank statement lines and their reconciliation with the books.

Statement lines are what the bank reported: uploaded once each (``StatementLine.key`` survives
overlapping downloads), never edited or deleted (banking 0002). Reconciling one links it to the
posted journal line that recorded the same movement, or posts that entry first (a bank charge,
interest, a transfer not recorded yet), or sets it aside as not the books' (ignored). A link can
be undone; the journal entry stays, and is reversed like any other if it was wrong.
"""

from datetime import date
from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.core.exceptions import InvalidStateTransition, ValidationFailed
from apps.core.money import quantize
from apps.core.sanitizers import sanitize_text
from apps.core.validators import ensure_business_date
from apps.ledger.domain import AccountCategory
from apps.ledger.models import Account, JournalEntry, JournalLine
from apps.ledger.services import JournalLineInput, create_journal_entry

from .domain import StatementLineStatus, line_key
from .models import StatementLine


def _check_bank_account(organization, account: Account) -> None:
    if account.organization_id != organization.pk or account.category != AccountCategory.CASH_BANK or not account.is_active:
        raise ValidationFailed(_("Choose an active cash or bank account."))


@transaction.atomic
def add_statement_line(
    *, organization, account: Account, on: date, amount: Decimal, description: str, reference: str = "",
    balance: Decimal | None = None, occurrence: int = 0,
) -> tuple[StatementLine, bool]:
    """Add a line as the bank reported it; (the line, whether it is new). A line already uploaded
    (same account, day, amount, text and balance) is returned as it is."""
    _check_bank_account(organization, account)
    ensure_business_date(on)
    amount = quantize(amount)
    if not amount:
        raise ValidationFailed(_("A statement line moves money in or out: its amount can't be 0."))
    description = sanitize_text(description)[:255] or "-"
    reference = sanitize_text(reference)[:100]
    balance = quantize(balance) if balance is not None else None
    key = line_key(account.pk, on, amount, description, balance, occurrence)
    existing = StatementLine.objects.for_organization(organization).filter(key=key).first()
    if existing is not None:
        return existing, False
    line = StatementLine.objects.create(
        organization=organization, account=account, date=on, amount=amount, description=description,
        reference=reference, balance=balance, key=key,
    )
    return line, True


def _locked(line: StatementLine) -> StatementLine:
    return StatementLine.objects.select_for_update().get(pk=line.pk)


def _check_match(line: StatementLine, journal_line: JournalLine) -> None:
    entry = journal_line.entry
    if entry.organization_id != line.organization_id or entry.status != JournalEntry.Status.POSTED:
        raise ValidationFailed(_("Choose a posted entry of this organization."))
    if journal_line.account_id != line.account_id:
        raise ValidationFailed(_("The entry must be on the statement's bank account."))
    expected = (line.amount, Decimal(0)) if line.amount > 0 else (Decimal(0), -line.amount)
    if (journal_line.debit, journal_line.credit) != expected:
        raise ValidationFailed(_("The entry's amount on the bank account must equal the statement line's."))
    if StatementLine.objects.filter(journal_line=journal_line).exclude(pk=line.pk).exists():
        raise ValidationFailed(_("That entry is already reconciled with another statement line."))


@transaction.atomic
def match_line(*, line: StatementLine, journal_line: JournalLine, matched_by=None) -> StatementLine:
    line = _locked(line)
    if line.status != StatementLineStatus.UNMATCHED:
        raise InvalidStateTransition(_("This statement line is already reconciled or ignored."))
    _check_match(line, journal_line)
    line.status, line.journal_line = StatementLineStatus.MATCHED, journal_line
    line.reconciled_by, line.reconciled_at = matched_by, timezone.now()
    line.save(update_fields=["status", "journal_line", "reconciled_by", "reconciled_at", "updated_at"])
    return line


@transaction.atomic
def post_and_match(*, line: StatementLine, counter_account: Account, description: str, created_by=None) -> StatementLine:
    """Record a movement the books don't have yet (bank charges, interest, a transfer) and reconcile it:
    a journal entry on the statement's date between the bank account and ``counter_account``."""
    line = _locked(line)
    if line.status != StatementLineStatus.UNMATCHED:
        raise InvalidStateTransition(_("This statement line is already reconciled or ignored."))
    if counter_account.organization_id != line.organization_id or counter_account.pk == line.account_id:
        raise ValidationFailed(_("Choose the account for the other side of the entry."))
    description = sanitize_text(description)[:255] or line.description
    amount = abs(line.amount)
    bank, other = (JournalLineInput(line.account, debit=amount), JournalLineInput(counter_account, credit=amount)) if line.amount > 0 else (
        JournalLineInput(line.account, credit=amount), JournalLineInput(counter_account, debit=amount))
    entry = create_journal_entry(
        organization=line.organization, date=line.date, description=description, reference=line.reference,
        lines=[bank, other], created_by=created_by,
    )
    journal_line = entry.lines.get(account=line.account)
    return match_line(line=line, journal_line=journal_line, matched_by=created_by)


@transaction.atomic
def ignore_line(*, line: StatementLine, ignored_by=None) -> StatementLine:
    """Set aside a line that isn't the books' to record (e.g. a reversed bank error)."""
    line = _locked(line)
    if line.status != StatementLineStatus.UNMATCHED:
        raise InvalidStateTransition(_("This statement line is already reconciled or ignored."))
    line.status, line.reconciled_by, line.reconciled_at = StatementLineStatus.IGNORED, ignored_by, timezone.now()
    line.save(update_fields=["status", "reconciled_by", "reconciled_at", "updated_at"])
    return line


@transaction.atomic
def undo_reconciliation(*, line: StatementLine) -> StatementLine:
    line = _locked(line)
    if line.status == StatementLineStatus.UNMATCHED:
        raise InvalidStateTransition(_("This statement line isn't reconciled."))
    line.status, line.journal_line, line.reconciled_by, line.reconciled_at = StatementLineStatus.UNMATCHED, None, None, None
    line.save(update_fields=["status", "journal_line", "reconciled_by", "reconciled_at", "updated_at"])
    return line

