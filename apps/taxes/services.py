from datetime import date

from django.db import transaction

from apps.core.exceptions import ValidationFailed
from apps.ledger.domain import SystemRole
from apps.ledger.models import JournalEntry
from apps.ledger.services import JournalLineInput, create_journal_entry, get_system_account, reverse_journal_entry

from .models import PphFinalPayment, TaxRate
from .selectors import pph_final_schedule

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


@transaction.atomic
def record_pph_final_payment(
    *, organization, year: int, month: int, paid_on: date, cash_account, ntpn: str = "", created_by=None
) -> PphFinalPayment:
    if not organization.uses_pph_final:
        raise ValidationFailed("Organisasi tidak menggunakan skema PPh Final UMKM.")
    if not cash_account.is_cash or cash_account.organization_id != organization.pk:
        raise ValidationFailed("Pilih akun kas/bank yang valid.")
    if PphFinalPayment.objects.for_organization(organization).filter(year=year, month=month).exists():
        raise ValidationFailed(f"PPh Final masa {month:02d}/{year} sudah dicatat.")
    period = pph_final_schedule(organization, year)[month - 1]
    if period.tax <= 0:
        raise ValidationFailed("Tidak ada PPh Final terutang untuk masa ini.")

    entry = create_journal_entry(
        organization=organization,
        date=paid_on,
        description=f"Setoran PPh Final UMKM masa {month:02d}/{year}",
        reference=ntpn,
        source=JournalEntry.Source.TAX,
        created_by=created_by,
        lines=[
            JournalLineInput(get_system_account(organization, SystemRole.PPH_FINAL_EXPENSE), debit=period.tax),
            JournalLineInput(cash_account, credit=period.tax),
        ],
    )
    return PphFinalPayment.objects.create(
        organization=organization,
        year=year,
        month=month,
        gross_turnover=period.gross_turnover,
        taxable_turnover=period.taxable_turnover,
        tax_amount=period.tax,
        paid_on=paid_on,
        ntpn=ntpn,
        cash_account=cash_account,
        journal_entry=entry,
        created_by=created_by,
    )


@transaction.atomic
def cancel_pph_final_payment(*, payment: PphFinalPayment, on: date, created_by=None) -> None:
    reverse_journal_entry(
        entry=payment.journal_entry, date=on, created_by=created_by,
        description=f"Pembatalan setoran PPh Final masa {payment.month:02d}/{payment.year}",
    )
    payment.delete()
