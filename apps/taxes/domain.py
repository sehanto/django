"""Indonesian tax calculations (pure functions, no database access)."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from django.db import models
from django.utils.translation import gettext_lazy as _
from django.utils.translation import pgettext_lazy

from apps.core.money import ZERO, quantize, round_down_rupiah

HUNDRED = Decimal("100")

# PP 55/2022 (previously PP 23/2018): final income tax for MSMEs.
PPH_FINAL_RATE = Decimal("0.005")
# Individual taxpayers are exempt on the first Rp500 juta of annual turnover.
PPH_FINAL_INDIVIDUAL_THRESHOLD = Decimal("500000000")
# Turnover limit for using the PPh Final scheme and for mandatory PKP status.
MSME_TURNOVER_LIMIT = Decimal("4800000000")
# PP 55/2022 Art. 59(1): the scheme may be used for a limited number of tax years, counted from
# the year the taxpayer registered, or from 2018 for those registered earlier (Art. 59(3)).
# Keyed by Organization.LegalForm: a sole proprietorship (UD) is its owner, an individual.
PPH_FINAL_TERM_YEARS = {"perorangan": 7, "cv": 4, "firma": 4, "koperasi": 4, "pt_perorangan": 4, "pt": 3}
EARLIEST_PPH_FINAL_YEAR = 2018


def pph_final_last_year(legal_form: str, first_year: int) -> int:
    """The last tax year PP 55/2022 allows the scheme for a taxpayer that first used it in ``first_year``."""
    return max(first_year, EARLIEST_PPH_FINAL_YEAR) + PPH_FINAL_TERM_YEARS[legal_form] - 1


def deposit_due(year: int, month: int) -> date:
    """When a month's PPh Final and withheld income tax must be deposited: the 15th of the next
    month (PMK 81/2024), or the next working day when that is a Saturday or Sunday. A national
    holiday moves it too; the calendar of those is not kept here."""
    due = date(year + month // 12, month % 12 + 1, 15)
    return due + timedelta(days=max(0, 7 - due.weekday())) if due.weekday() >= 5 else due


class Month(models.IntegerChoices):
    """Calendar month of a tax period (masa pajak)."""

    JANUARY = 1, pgettext_lazy("month name", "January")
    FEBRUARY = 2, pgettext_lazy("month name", "February")
    MARCH = 3, pgettext_lazy("month name", "March")
    APRIL = 4, pgettext_lazy("month name", "April")
    MAY = 5, pgettext_lazy("month name", "May")
    JUNE = 6, pgettext_lazy("month name", "June")
    JULY = 7, pgettext_lazy("month name", "July")
    AUGUST = 8, pgettext_lazy("month name", "August")
    SEPTEMBER = 9, pgettext_lazy("month name", "September")
    OCTOBER = 10, pgettext_lazy("month name", "October")
    NOVEMBER = 11, pgettext_lazy("month name", "November")
    DECEMBER = 12, pgettext_lazy("month name", "December")

    def tax_period(self, year: int) -> str:
        """The masa pajak as written on tax forms and journal entries, e.g. ``03/2026``."""
        return f"{self.value:02d}/{year}"


class PayerWithholding(models.TextChoices):
    """Income tax the business withholds from what it pays suppliers (as the payer), and
    deposits monthly. The values match ``invoicing.IncomeTaxWithheld``."""

    # xgettext:no-python-format
    PPH_23 = "pph23", _("PPh 23 (e.g. 2% on services)")
    # xgettext:no-python-format
    PPH_4_2 = "pph4_2", _("PPh 4(2) final (e.g. 10% on rent of land or buildings)")
    PPH_21 = "pph21", _("PPh 21 (salaries and fees)")
    PPH_26 = "pph26", _("PPh 26 (non-resident employees)")


# How deposits name them in journal descriptions (stored records stay Indonesian).
PAYER_WITHHOLDING_NAMES = {
    PayerWithholding.PPH_23: "PPh 23", PayerWithholding.PPH_4_2: "PPh Pasal 4 ayat (2)", PayerWithholding.PPH_21: "PPh 21",
    PayerWithholding.PPH_26: "PPh 26",
}


# The usual rate of each, suggested on the payment form.
PAYER_WITHHOLDING_RATES = {PayerWithholding.PPH_23: Decimal("2"), PayerWithholding.PPH_4_2: Decimal("10")}


@dataclass(frozen=True)
class VatResult:
    dpp: Decimal  # Dasar Pengenaan Pajak (tax base)
    tax: Decimal


def compute_vat(base: Decimal, rate_percent: Decimal, dpp_numerator: int = 1, dpp_denominator: int = 1) -> VatResult:
    """PPN = DPP x tarif, rounded down to whole rupiah.

    From 2025 (PMK 131/2024) the statutory rate is 12%, but non-luxury goods
    and services use DPP "nilai lain" of 11/12 of the selling price, which
    makes the effective rate 11%.
    """
    if base <= 0 or rate_percent <= 0:
        return VatResult(dpp=quantize(max(base, ZERO)), tax=ZERO)
    dpp = quantize(base * Decimal(dpp_numerator) / Decimal(dpp_denominator))
    return VatResult(dpp=dpp, tax=round_down_rupiah(dpp * rate_percent / HUNDRED))


@dataclass(frozen=True)
class PphFinalMonth:
    month: Month
    gross_turnover: Decimal
    cumulative_before: Decimal
    exempt_turnover: Decimal
    taxable_turnover: Decimal
    tax: Decimal


def compute_pph_final(monthly_turnover: Sequence[Decimal], *, is_individual: bool) -> list[PphFinalMonth]:
    """Monthly PPh Final for one tax year.

    ``monthly_turnover[i]`` is the gross turnover (peredaran bruto) of month
    ``i + 1``. For individuals the Rp500 juta exemption is consumed
    chronologically through the year.
    """
    threshold = PPH_FINAL_INDIVIDUAL_THRESHOLD if is_individual else ZERO
    cumulative = ZERO
    result = []
    for index, gross in enumerate(monthly_turnover, start=1):
        gross = quantize(max(gross, ZERO))
        remaining_exemption = max(threshold - cumulative, ZERO)
        exempt = min(gross, remaining_exemption)
        taxable = gross - exempt
        result.append(
            PphFinalMonth(
                month=Month(index),
                gross_turnover=gross,
                cumulative_before=cumulative,
                exempt_turnover=exempt,
                taxable_turnover=taxable,
                tax=round_down_rupiah(taxable * PPH_FINAL_RATE),
            )
        )
        cumulative += gross
    return result
