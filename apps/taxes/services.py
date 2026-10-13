import calendar
from datetime import date

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.core.exceptions import InvalidStateTransition, ValidationFailed
from apps.core.formatting import localized_rupiah
from apps.core.money import ZERO
from apps.ledger.domain import SystemRole
from apps.ledger.models import JournalEntry
from apps.ledger.services import JournalLineInput, create_journal_entry, get_system_account, reverse_journal_entry

from .domain import PAYER_WITHHOLDING_NAMES, Month, PayerWithholding
from .models import PphFinalAccrual, PphFinalPayment, TaxRate, WithholdingDeposit
from .selectors import pph_final_schedule, pph_final_withheld, withholding_due

DEFAULT_TAX_RATES = [
    # (name, rate, dpp_numerator, dpp_denominator)
    ("PPN 12% (DPP 11/12) - Umum", 12, 11, 12),
    ("PPN 12% - Barang Mewah", 12, 1, 1),
]


def seed_tax_rates(organization) -> None:
    TaxRate.objects.bulk_create(
        [
            TaxRate(organization=organization, name=name, rate=rate, dpp_numerator=num, dpp_denominator=den)
            for name, rate, num, den in DEFAULT_TAX_RATES
        ]
    )


def _period(month: Month, year: int) -> dict:
    return {"period": f"{month.label} {year}"}


def month_end(year: int, month: Month) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def _ensure_uses_pph_final(organization) -> None:
    if not organization.uses_pph_final:
        raise ValidationFailed(_("This organization does not use the MSME PPh Final scheme."))


def posted_accrual(organization, year: int, month: Month) -> PphFinalAccrual | None:
    return (
        PphFinalAccrual.objects.for_organization(organization).select_for_update()
        .filter(year=year, month=month, status=PphFinalAccrual.Status.POSTED).first()
    )


@transaction.atomic
def accrue_pph_final(*, organization, year: int, month: Month, created_by=None) -> PphFinalAccrual:
    """Recognise the month's PPh Final as an expense on its last day (accrual basis, SAK EMKM):
    Beban PPh Final against Utang PPh Final, which the deposit settles."""
    month = Month(month)
    _ensure_uses_pph_final(organization)
    if month_end(year, month) > timezone.localdate():
        raise ValidationFailed(_("%(period)s has not ended yet. Record its PPh Final expense from the last day of the month.") % _period(month, year))
    if posted_accrual(organization, year, month):
        raise ValidationFailed(_("The PPh Final expense for %(period)s has already been recorded.") % _period(month, year))
    if PphFinalPayment.objects.for_organization(organization).filter(year=year, month=month, status=PphFinalPayment.Status.POSTED).exists():
        raise ValidationFailed(_("PPh Final for %(period)s has already been recorded.") % _period(month, year))
    period = pph_final_schedule(organization, year)[month - 1]
    if period.tax <= 0:
        raise ValidationFailed(_("No PPh Final is due for this period."))

    # Journal descriptions are stored business records and stay in Indonesian.
    entry = create_journal_entry(
        organization=organization,
        date=month_end(year, month),
        description=f"Beban PPh Final UMKM masa {month.tax_period(year)}",
        source=JournalEntry.Source.TAX,
        created_by=created_by,
        lines=[
            JournalLineInput(get_system_account(organization, SystemRole.PPH_FINAL_EXPENSE), debit=period.tax),
            JournalLineInput(get_system_account(organization, SystemRole.PPH_FINAL_PAYABLE), credit=period.tax),
        ],
    )
    return PphFinalAccrual.objects.create(
        organization=organization,
        year=year,
        month=month,
        gross_turnover=period.gross_turnover,
        taxable_turnover=period.taxable_turnover,
        tax_amount=period.tax,
        journal_entry=entry,
        created_by=created_by,
    )


@transaction.atomic
def cancel_pph_final_accrual(*, accrual: PphFinalAccrual, on: date, created_by=None) -> PphFinalAccrual:
    accrual = PphFinalAccrual.objects.select_for_update().get(pk=accrual.pk)
    if accrual.status != PphFinalAccrual.Status.POSTED:
        raise InvalidStateTransition(_("This PPh Final expense has already been cancelled."))
    if accrual.payments.filter(status=PphFinalPayment.Status.POSTED).exists():
        raise InvalidStateTransition(_("Cancel the payment of this PPh Final first."))
    reverse_journal_entry(
        entry=accrual.journal_entry, date=on, created_by=created_by,
        description=f"Pembatalan beban PPh Final masa {Month(accrual.month).tax_period(accrual.year)}",
    )
    accrual.status = PphFinalAccrual.Status.VOID  # kept as history: never deleted
    accrual.save(update_fields=["status", "updated_at"])
    return accrual


@transaction.atomic
def record_pph_final_payment(
    *, organization, year: int, month: Month, paid_on: date, cash_account, ntpn: str = "", created_by=None
) -> PphFinalPayment:
    """Deposit (setoran) of a month's PPh Final. It settles the month's recorded expense,
    which is recorded first when it is missing, so the expense always falls in its own month.
    PPh Final that buyers withheld from the month's receipts already settled part of it (the
    buyers deposit it), so only the rest is paid."""
    month = Month(month)
    _ensure_uses_pph_final(organization)
    if not cash_account.is_cash or cash_account.organization_id != organization.pk:
        raise ValidationFailed(_("Choose a valid cash or bank account."))
    if PphFinalPayment.objects.for_organization(organization).filter(year=year, month=month, status=PphFinalPayment.Status.POSTED).exists():
        raise ValidationFailed(_("PPh Final for %(period)s has already been recorded.") % _period(month, year))
    accrual = posted_accrual(organization, year, month) or accrue_pph_final(
        organization=organization, year=year, month=month, created_by=created_by
    )
    withheld = pph_final_withheld(organization, year).get(month, ZERO)
    if withheld >= accrual.tax_amount:
        raise ValidationFailed(
            _("Buyers withheld %(withheld)s of PPh Final in %(period)s, which covers the %(due)s due: there is nothing to deposit.")
            % {"withheld": localized_rupiah(withheld), "due": localized_rupiah(accrual.tax_amount), **_period(month, year)}
        )

    entry = create_journal_entry(
        organization=organization,
        date=paid_on,
        description=f"Setoran PPh Final UMKM masa {month.tax_period(year)}",
        reference=ntpn,
        source=JournalEntry.Source.TAX,
        created_by=created_by,
        lines=[
            JournalLineInput(get_system_account(organization, SystemRole.PPH_FINAL_PAYABLE), debit=accrual.tax_amount - withheld),
            JournalLineInput(cash_account, credit=accrual.tax_amount - withheld),
        ],
    )
    return PphFinalPayment.objects.create(
        organization=organization,
        year=year,
        month=month,
        gross_turnover=accrual.gross_turnover,
        taxable_turnover=accrual.taxable_turnover,
        tax_amount=accrual.tax_amount,
        withheld=withheld,
        paid_on=paid_on,
        ntpn=ntpn,
        cash_account=cash_account,
        journal_entry=entry,
        accrual=accrual,
        created_by=created_by,
    )


@transaction.atomic
def cancel_pph_final_payment(*, payment: PphFinalPayment, on: date, created_by=None) -> PphFinalPayment:
    payment = PphFinalPayment.objects.select_for_update().get(pk=payment.pk)
    if payment.status != PphFinalPayment.Status.POSTED:
        raise InvalidStateTransition(_("This PPh Final payment has already been cancelled."))
    reverse_journal_entry(
        entry=payment.journal_entry, date=on, created_by=created_by,
        description=f"Pembatalan setoran PPh Final masa {Month(payment.month).tax_period(payment.year)}",
    )
    payment.status = PphFinalPayment.Status.VOID  # kept as history: never deleted
    payment.save(update_fields=["status", "updated_at"])
    return payment


PAYER_ROLES = {
    PayerWithholding.PPH_23: SystemRole.PPH_23_PAYABLE, PayerWithholding.PPH_4_2: SystemRole.PPH_4_2_PAYABLE,
    PayerWithholding.PPH_21: SystemRole.PPH_21_PAYABLE, PayerWithholding.PPH_26: SystemRole.PPH_26_PAYABLE,
}


@transaction.atomic
def record_withholding_deposit(
    *, organization, kind: str, year: int, month: Month, paid_on: date, cash_account, ntpn: str = "", created_by=None,
) -> WithholdingDeposit:
    """Deposit (setoran) of the PPh 23, 4(2), 21 or 26 withheld in a month: it settles the
    withheld tax payable. Due by the 15th of the next month."""
    month = Month(month)
    kind = PayerWithholding(kind)
    if not cash_account.is_cash or cash_account.organization_id != organization.pk:
        raise ValidationFailed(_("Choose a valid cash or bank account."))
    if WithholdingDeposit.objects.for_organization(organization).filter(kind=kind, year=year, month=month, status=WithholdingDeposit.Status.POSTED).exists():
        raise ValidationFailed(_("The %(tax)s for %(period)s has already been deposited.") % {"tax": kind.label, **_period(month, year)})
    amount = withholding_due(organization, year).get((kind, month), ZERO)
    if amount <= 0:
        raise ValidationFailed(_("No %(tax)s was withheld in %(period)s.") % {"tax": kind.label, **_period(month, year)})
    tax = PAYER_WITHHOLDING_NAMES[kind]
    entry = create_journal_entry(
        organization=organization, date=paid_on, description=f"Setoran {tax} masa {month.tax_period(year)}", reference=ntpn,
        source=JournalEntry.Source.TAX, created_by=created_by,
        lines=[
            JournalLineInput(get_system_account(organization, PAYER_ROLES[kind]), debit=amount),
            JournalLineInput(cash_account, credit=amount),
        ],
    )
    return WithholdingDeposit.objects.create(
        organization=organization, kind=kind, year=year, month=month, amount=amount, paid_on=paid_on, ntpn=ntpn,
        cash_account=cash_account, journal_entry=entry, created_by=created_by,
    )


@transaction.atomic
def cancel_withholding_deposit(*, deposit: WithholdingDeposit, on: date, created_by=None) -> WithholdingDeposit:
    deposit = WithholdingDeposit.objects.select_for_update().get(pk=deposit.pk)
    if deposit.status != WithholdingDeposit.Status.POSTED:
        raise InvalidStateTransition(_("This deposit has already been cancelled."))
    reverse_journal_entry(
        entry=deposit.journal_entry, date=max(on, deposit.paid_on), created_by=created_by,
        description=f"Pembatalan setoran {deposit.get_kind_display()} masa {Month(deposit.month).tax_period(deposit.year)}"[:255],
    )
    deposit.status = WithholdingDeposit.Status.VOID  # kept as history: never deleted
    deposit.save(update_fields=["status", "updated_at"])
    return deposit
