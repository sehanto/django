"""Write-side use cases for the general ledger.

Every automated module (invoicing, payments, taxes) records its accounting
impact through ``create_journal_entry`` so that double-entry and period-lock
invariants are enforced in exactly one place.

The database enforces the same invariants independently (triggers in
``ledger/migrations/0004_immutable_ledger.py``): entries are inserted as drafts,
become posted only through a balanced ``draft -> posted`` update, and posted
entries and their lines can never be updated or deleted. The only exception is
``purge_organization_ledger`` (tenant offboarding, migration 0005). Posting also
appends the entry to the organization's hash chain (migration 0006), which
``manage.py verify_ledger`` checks.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.db import connection, transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.core.exceptions import InvalidStateTransition, ValidationFailed
from apps.core.money import ZERO, quantize
from apps.core.sequences import next_document_number
from apps.core.validators import ensure_business_date

from .chart_template import DEFAULT_CHART
from .domain import LineAmount, SystemRole, ensure_period_open, validate_lines
from .models import Account, JournalEntry, JournalLine

Source = JournalEntry.Source

NUMBER_PREFIX = {
    Source.MANUAL: "JU",
    Source.SALES_INVOICE: "JP",
    Source.PURCHASE_INVOICE: "JB",
    Source.PAYMENT: "JK",
    Source.TAX: "JT",
    Source.REVERSAL: "JR",
}


@dataclass(frozen=True)
class JournalLineInput:
    account: Account
    debit: Decimal = ZERO
    credit: Decimal = ZERO
    description: str = ""


# --- Chart of accounts -------------------------------------------------------


def seed_chart_of_accounts(organization) -> None:
    Account.objects.bulk_create(
        [
            Account(
                organization=organization,
                code=code,
                name=name,
                category=category,
                system_role=role,
                is_contra=is_contra,
            )
            for code, name, category, role, is_contra in DEFAULT_CHART
        ]
    )


def get_system_account(organization, role: SystemRole) -> Account:
    account = Account.objects.for_organization(organization).filter(system_role=role).first()
    if account is None:
        raise ValidationFailed(
            _("The system account '%(role)s' is not set in the chart of accounts.") % {"role": SystemRole(role).label}
        )
    return account


def _has_postings(account: Account) -> bool:
    return account.lines.exists()


@transaction.atomic
def create_account(*, organization, **fields) -> Account:
    if Account.objects.for_organization(organization).filter(code=fields["code"]).exists():
        raise ValidationFailed(_("Account code %(code)s is already in use.") % {"code": fields["code"]})
    return Account.objects.create(organization=organization, **fields)


@transaction.atomic
def update_account(*, account: Account, **fields) -> Account:
    changes_meaning = any(
        name in fields and fields[name] != getattr(account, name) for name in Account.FIXED_ONCE_USED
    )
    if changes_meaning and _has_postings(account):
        raise ValidationFailed(_("The category and contra setting of an account with transactions cannot be changed."))
    if account.system_role and fields.get("is_active") is False:
        raise ValidationFailed(_("System accounts cannot be deactivated."))
    duplicate = (
        Account.objects.for_organization(account.organization)
        .filter(code=fields.get("code", account.code))
        .exclude(pk=account.pk)
    )
    if duplicate.exists():
        raise ValidationFailed(_("This account code is already in use."))
    for name, value in fields.items():
        setattr(account, name, value)
    account.save()
    return account


@transaction.atomic
def delete_account(*, account: Account) -> None:
    if account.system_role:
        raise ValidationFailed(_("System accounts cannot be deleted."))
    if _has_postings(account):
        raise ValidationFailed(_("Accounts with transactions cannot be deleted; deactivate them instead."))
    account.delete()


# --- Journal entries ---------------------------------------------------------


def _validate(organization, entry_date: date, lines: Sequence[JournalLineInput], posting: bool) -> None:
    ensure_business_date(entry_date)
    validate_lines(LineAmount(line.debit, line.credit) for line in lines)
    for line in lines:
        if line.account.organization_id != organization.pk:
            raise ValidationFailed(_("Account not found in this organization."))
        if not line.account.is_active:
            raise ValidationFailed(_("Account %(account)s is inactive.") % {"account": line.account})
    if posting:
        ensure_period_open(entry_date, organization.lock_date)


def _write_lines(entry: JournalEntry, lines: Sequence[JournalLineInput]) -> None:
    JournalLine.objects.bulk_create(
        [
            JournalLine(
                entry=entry,
                account=line.account,
                description=line.description,
                debit=quantize(line.debit),
                credit=quantize(line.credit),
            )
            for line in lines
        ]
    )


@transaction.atomic
def create_journal_entry(
    *,
    organization,
    date: date,
    description: str,
    lines: Sequence[JournalLineInput],
    source: str = Source.MANUAL,
    reference: str = "",
    created_by=None,
    post: bool = True,
    reversal_of: JournalEntry | None = None,
) -> JournalEntry:
    _validate(organization, date, lines, posting=post)
    entry = JournalEntry.objects.create(
        organization=organization,
        number=next_document_number(organization, NUMBER_PREFIX[source], date),
        date=date,
        description=description,
        reference=reference,
        source=source,
        status=JournalEntry.Status.DRAFT,  # the database only accepts posting as a draft -> posted update
        reversal_of=reversal_of,
        created_by=created_by,
    )
    _write_lines(entry, lines)
    if post:
        _mark_posted(entry, posted_by=created_by)
    return entry


def _mark_posted(entry: JournalEntry, *, posted_by) -> None:
    entry.status = JournalEntry.Status.POSTED
    entry.posted_at = timezone.now()
    entry.posted_by = posted_by
    entry.save(update_fields=["status", "posted_at", "posted_by", "updated_at"])
    # The database trigger appended the entry to the organization's hash chain.
    entry.refresh_from_db(fields=["posting_sequence", "previous_hash", "entry_hash"])


def _ensure_editable_manual_draft(entry: JournalEntry) -> None:
    if entry.status != JournalEntry.Status.DRAFT:
        raise InvalidStateTransition(_("Only draft journal entries can be changed."))
    if entry.source != Source.MANUAL:
        raise InvalidStateTransition(_("Automatic journal entries can only be changed through their source document."))


@transaction.atomic
def update_draft_journal_entry(
    *, entry: JournalEntry, date: date, description: str, reference: str, lines: Sequence[JournalLineInput]
) -> JournalEntry:
    _ensure_editable_manual_draft(entry)
    _validate(entry.organization, date, lines, posting=False)
    entry.date, entry.description, entry.reference = date, description, reference
    entry.save()
    entry.lines.all().delete()
    _write_lines(entry, lines)
    return entry


@transaction.atomic
def post_journal_entry(*, entry: JournalEntry, posted_by=None) -> JournalEntry:
    entry = JournalEntry.objects.select_for_update().get(pk=entry.pk)
    if entry.status != JournalEntry.Status.DRAFT:
        raise InvalidStateTransition(_("The journal entry is already posted."))
    lines = [JournalLineInput(l.account, l.debit, l.credit, l.description) for l in entry.lines.select_related("account")]
    _validate(entry.organization, entry.date, lines, posting=True)
    _mark_posted(entry, posted_by=posted_by)
    return entry


@transaction.atomic
def delete_draft_journal_entry(*, entry: JournalEntry) -> None:
    _ensure_editable_manual_draft(entry)
    entry.delete()


@transaction.atomic
def reverse_journal_entry(*, entry: JournalEntry, date: date, created_by=None, description: str = "") -> JournalEntry:
    """Cancel a posted entry by recording its mirror image (never deleting)."""
    entry = JournalEntry.objects.select_for_update().get(pk=entry.pk)
    if not entry.is_posted:
        raise InvalidStateTransition(_("Only posted journal entries can be reversed."))
    if entry.is_reversed:
        raise InvalidStateTransition(_("This journal entry has already been reversed."))
    if entry.source == Source.REVERSAL:
        raise InvalidStateTransition(_("A reversing entry cannot itself be reversed."))
    mirrored = [
        JournalLineInput(line.account, debit=line.credit, credit=line.debit, description=line.description)
        for line in entry.lines.select_related("account")
    ]
    reversal = create_journal_entry(
        organization=entry.organization,
        date=date,
        description=description or f"Pembalikan {entry.number}: {entry.description}",
        lines=mirrored,
        source=Source.REVERSAL,
        reference=entry.number,
        created_by=created_by,
        reversal_of=entry,
    )
    return reversal


def reverse_manual_journal_entry(*, entry: JournalEntry, date: date, created_by=None) -> JournalEntry:
    if entry.source != Source.MANUAL:
        raise InvalidStateTransition(_("Automatic journal entries are cancelled through their source document (invoice or payment)."))
    return reverse_journal_entry(entry=entry, date=date, created_by=created_by)


@transaction.atomic
def purge_organization_ledger(organization) -> None:
    """Delete a tenant's books (journal entries, invoices, payments, PPh Final payments)
    when it is offboarded.

    The only sanctioned way to remove posted entries and issued documents. The data-only
    app role cannot lift the database guards itself; ``ledger_purge_organization`` (a
    SECURITY DEFINER function owned by the schema owner, migrations 0005 and 0007) does
    it for exactly one whole organization.
    """
    with connection.cursor() as cursor:
        cursor.execute("SELECT ledger_purge_organization(%s)", [organization.pk])
