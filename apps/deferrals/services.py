"""Prepayments register: writes. Every amount moves through the ledger."""

from datetime import date

from django.db import transaction
from django.utils.translation import gettext as _

from apps.core.exceptions import InvalidStateTransition, ValidationFailed
from apps.core.money import quantize
from apps.core.periods import month_end
from apps.core.sequences import next_document_number
from apps.core.validators import ensure_business_date
from apps.ledger.models import JournalEntry
from apps.ledger.services import JournalLineInput, create_journal_entry, reverse_journal_entry

from .domain import MAX_TERM_MONTHS, DeferralKind
from .models import Deferral, DeferralPosting
from .selectors import BALANCE_CATEGORIES, RECOGNITION_CATEGORIES, due_recognition

NUMBER_PREFIX = "DM"  # dibayar/diterima di muka
# Journal descriptions are stored business records and stay in Indonesian.
WHAT = {
    DeferralKind.PREPAID_EXPENSE: "beban dibayar di muka",
    DeferralKind.DEFERRED_REVENUE: "pendapatan diterima di muka",
}


def _check_accounts(organization, kind: str, balance_account, recognition_account) -> None:
    if not (balance_account is not None and balance_account.organization_id == organization.pk and balance_account.is_active
            and balance_account.category in BALANCE_CATEGORIES[kind] and not balance_account.system_role and not balance_account.is_contra):
        if kind == DeferralKind.PREPAID_EXPENSE:
            raise ValidationFailed(_("Choose an other current asset account for the amount paid in advance, e.g. Biaya Dibayar di Muka."))
        raise ValidationFailed(_("Choose a liability account for the amount received in advance, e.g. Pendapatan Diterima di Muka."))
    if not (recognition_account is not None and recognition_account.organization_id == organization.pk and recognition_account.is_active
            and recognition_account.category in RECOGNITION_CATEGORIES[kind] and not recognition_account.is_contra):
        if kind == DeferralKind.PREPAID_EXPENSE:
            raise ValidationFailed(_("Choose the expense account the amount is spread to, e.g. Beban Sewa."))
        raise ValidationFailed(_("Choose the revenue account the amount is spread to, e.g. Pendapatan Sewa."))


def _check_term(recorded_on: date, starts_on: date, months: int) -> None:
    ensure_business_date(recorded_on)
    ensure_business_date(starts_on)
    if not 1 <= months <= MAX_TERM_MONTHS:
        raise ValidationFailed(_("The term is between 1 and %(months)s months.") % {"months": MAX_TERM_MONTHS})
    if recorded_on > month_end(starts_on.year, starts_on.month):
        raise ValidationFailed(_("An amount paid or received in advance is recorded by the end of the first month it covers."))


def _opening_lines(kind: str, balance_account, recognition_account, amount) -> list[JournalLineInput]:
    """Move an amount recorded as expense or revenue to the prepaid or received-in-advance account."""
    if kind == DeferralKind.PREPAID_EXPENSE:
        return [JournalLineInput(balance_account, debit=amount), JournalLineInput(recognition_account, credit=amount)]
    return [JournalLineInput(recognition_account, debit=amount), JournalLineInput(balance_account, credit=amount)]


def _recognition_lines(deferral: Deferral, amount) -> list[JournalLineInput]:
    label = f"{deferral.number} {deferral.description}"[:255]
    if deferral.is_prepaid_expense:
        return [JournalLineInput(deferral.recognition_account, debit=amount, description=label),
                JournalLineInput(deferral.balance_account, credit=amount, description=label)]
    return [JournalLineInput(deferral.balance_account, debit=amount, description=label),
            JournalLineInput(deferral.recognition_account, credit=amount, description=label)]


@transaction.atomic
def register_deferral(
    *, organization, kind: str, description: str, amount, recorded_on: date, starts_on: date, months: int,
    balance_account, recognition_account, reclassify: bool, created_by=None,
) -> Deferral:
    """Spread an amount paid or received in advance over the months it covers.

    ``reclassify``: the bill, invoice or payment recorded it as an expense or revenue, so it is
    first moved to the balance account on ``recorded_on``; otherwise it is there already.
    """
    if kind not in DeferralKind.values:
        raise ValidationFailed(_("Choose whether the amount was paid or received in advance."))
    description, amount = description.strip(), quantize(amount)
    if not description:
        raise ValidationFailed(_("Describe what the amount covers."))
    if amount <= 0:
        raise ValidationFailed(_("The amount must be positive."))
    starts_on = starts_on.replace(day=1)
    _check_term(recorded_on, starts_on, months)
    _check_accounts(organization, kind, balance_account, recognition_account)
    number = next_document_number(organization, NUMBER_PREFIX, starts_on)
    opening = None
    if reclassify:
        opening = create_journal_entry(
            organization=organization, date=recorded_on, description=f"Reklasifikasi {WHAT[kind]} {number} {description}"[:255],
            source=JournalEntry.Source.DEFERRAL, created_by=created_by,
            lines=_opening_lines(kind, balance_account, recognition_account, amount),
        )
    return Deferral.objects.create(
        organization=organization, number=number, kind=kind, description=description, amount=amount, recorded_on=recorded_on,
        starts_on=starts_on, months=months, balance_account=balance_account, recognition_account=recognition_account,
        opening_entry=opening, created_by=created_by,
    )


@transaction.atomic
def post_deferrals(*, organization, through: date, created_by=None) -> list[JournalEntry]:
    """Recognise every month that has ended by ``through`` and isn't recognised yet: one journal
    entry per month, dated its last day.

    Raises ``PeriodLocked`` when a month due falls on or before the lock date; nothing is
    posted then, so the register never skips a month.
    """
    entries = []
    for (year, month), items in due_recognition(organization, through).items():
        kinds = sorted({deferral.kind for deferral, _month in items})
        what = " dan ".join(WHAT[kind] for kind in kinds)
        entry = create_journal_entry(
            organization=organization, date=month_end(year, month), description=f"Pengakuan {what} masa {month:02d}/{year}",
            source=JournalEntry.Source.DEFERRAL, created_by=created_by,
            lines=[line for deferral, scheduled in items for line in _recognition_lines(deferral, scheduled.amount)],
        )
        DeferralPosting.objects.bulk_create(
            DeferralPosting(organization=organization, deferral=deferral, year=year, month=month, amount=scheduled.amount, journal_entry=entry)
            for deferral, scheduled in items
        )
        entries.append(entry)
    return entries


@transaction.atomic
def void_deferral(*, deferral: Deferral, on: date, voided_by=None) -> Deferral:
    """Undo a deferral registered by mistake, before any month is recognised; its opening
    reclassification is reversed."""
    deferral = Deferral.objects.select_for_update(of=("self",)).select_related("opening_entry").get(pk=deferral.pk)
    if deferral.status != Deferral.Status.ACTIVE:
        raise InvalidStateTransition(_("This prepayment has already been voided."))
    if deferral.postings.exists():
        raise InvalidStateTransition(_("Months of this prepayment have been recognised, so it stays in the register."))
    if deferral.opening_entry is not None:
        reverse_journal_entry(entry=deferral.opening_entry, date=max(on, deferral.opening_entry.date), created_by=voided_by,
                              description=f"Pembatalan {deferral.number}")
    deferral.status = Deferral.Status.VOID
    deferral.save(update_fields=["status", "updated_at"])
    return deferral
