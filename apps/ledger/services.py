"""Write-side use cases for the general ledger.

Every automated module (invoicing, payments, taxes) records its accounting
impact through ``create_journal_entry`` so that double-entry and period-lock
invariants are enforced in exactly one place.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.core.exceptions import InvalidStateTransition, ValidationFailed
from apps.core.money import ZERO, quantize
from apps.core.sequences import next_document_number

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
        raise ValidationFailed(f"Akun sistem '{SystemRole(role).label}' belum ditetapkan di bagan akun.")
    return account


def _has_postings(account: Account) -> bool:
    return account.lines.exists()


@transaction.atomic
def create_account(*, organization, **fields) -> Account:
    if Account.objects.for_organization(organization).filter(code=fields["code"]).exists():
        raise ValidationFailed(f"Kode akun {fields['code']} sudah digunakan.")
    return Account.objects.create(organization=organization, **fields)


@transaction.atomic
def update_account(*, account: Account, **fields) -> Account:
    if (
        "category" in fields
        and fields["category"] != account.category
        and _has_postings(account)
    ):
        raise ValidationFailed("Kategori akun yang sudah memiliki transaksi tidak dapat diubah.")
    if account.system_role and fields.get("is_active") is False:
        raise ValidationFailed("Akun sistem tidak dapat dinonaktifkan.")
    duplicate = (
        Account.objects.for_organization(account.organization)
        .filter(code=fields.get("code", account.code))
        .exclude(pk=account.pk)
    )
    if duplicate.exists():
        raise ValidationFailed("Kode akun sudah digunakan.")
    for name, value in fields.items():
        setattr(account, name, value)
    account.save()
    return account


@transaction.atomic
def delete_account(*, account: Account) -> None:
    if account.system_role:
        raise ValidationFailed("Akun sistem tidak dapat dihapus.")
    if _has_postings(account):
        raise ValidationFailed("Akun yang sudah memiliki transaksi tidak dapat dihapus; nonaktifkan saja.")
    account.delete()


# --- Journal entries ---------------------------------------------------------


def _validate(organization, entry_date: date, lines: Sequence[JournalLineInput], posting: bool) -> None:
    validate_lines(LineAmount(line.debit, line.credit) for line in lines)
    for line in lines:
        if line.account.organization_id != organization.pk:
            raise ValidationFailed("Akun tidak ditemukan pada organisasi ini.")
        if not line.account.is_active:
            raise ValidationFailed(f"Akun {line.account} tidak aktif.")
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
) -> JournalEntry:
    _validate(organization, date, lines, posting=post)
    entry = JournalEntry.objects.create(
        organization=organization,
        number=next_document_number(organization, NUMBER_PREFIX[source], date),
        date=date,
        description=description,
        reference=reference,
        source=source,
        status=JournalEntry.Status.POSTED if post else JournalEntry.Status.DRAFT,
        posted_at=timezone.now() if post else None,
        created_by=created_by,
    )
    _write_lines(entry, lines)
    return entry


def _ensure_editable_manual_draft(entry: JournalEntry) -> None:
    if entry.status != JournalEntry.Status.DRAFT:
        raise InvalidStateTransition("Hanya jurnal berstatus draf yang dapat diubah.")
    if entry.source != Source.MANUAL:
        raise InvalidStateTransition("Jurnal otomatis hanya dapat diubah melalui dokumen sumbernya.")


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
def post_journal_entry(*, entry: JournalEntry) -> JournalEntry:
    entry = JournalEntry.objects.select_for_update().get(pk=entry.pk)
    if entry.status != JournalEntry.Status.DRAFT:
        raise InvalidStateTransition("Jurnal sudah diposting.")
    lines = [JournalLineInput(l.account, l.debit, l.credit, l.description) for l in entry.lines.select_related("account")]
    _validate(entry.organization, entry.date, lines, posting=True)
    entry.status = JournalEntry.Status.POSTED
    entry.posted_at = timezone.now()
    entry.save(update_fields=["status", "posted_at", "updated_at"])
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
        raise InvalidStateTransition("Hanya jurnal yang sudah diposting yang dapat dibalik.")
    if entry.is_reversed:
        raise InvalidStateTransition("Jurnal ini sudah pernah dibalik.")
    if entry.source == Source.REVERSAL:
        raise InvalidStateTransition("Jurnal pembalik tidak dapat dibalik lagi.")
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
    )
    reversal.reversal_of = entry
    reversal.save(update_fields=["reversal_of"])
    return reversal


def reverse_manual_journal_entry(*, entry: JournalEntry, date: date, created_by=None) -> JournalEntry:
    if entry.source != Source.MANUAL:
        raise InvalidStateTransition("Jurnal otomatis dibatalkan melalui dokumen sumbernya (faktur/pembayaran).")
    return reverse_journal_entry(entry=entry, date=date, created_by=created_by)
