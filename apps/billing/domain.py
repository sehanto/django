"""Subscription billing rules (pure functions, no database access).

Plans are prepaid: each payment buys one period. Paying again for the same plan
extends the current period; buying a different plan starts a new period on the
payment date. Prices exclude PPN, which is added at checkout.
"""

import calendar
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.money import ZERO, quantize, round_down_rupiah
from apps.taxes.domain import compute_vat

# PMK 131/2024: 12% on DPP "nilai lain" of 11/12 (effectively 11%) for non-luxury services.
SUBSCRIPTION_PPN_RATE = Decimal("12")
SUBSCRIPTION_DPP = (11, 12)


class BillingPeriod(models.TextChoices):
    MONTHLY = "monthly", _("Monthly")
    YEARLY = "yearly", _("Yearly")

    @property
    def months(self) -> int:
        return 12 if self == BillingPeriod.YEARLY else 1


@dataclass(frozen=True)
class Charge:
    subtotal: Decimal  # plan price, excluding PPN
    tax_base: Decimal  # DPP
    tax: Decimal
    total: Decimal
    credit: Decimal = ZERO  # proration credit deducted before tax

    @property
    def net(self) -> Decimal:
        """Amount excluding PPN after the credit: the base PPN is charged on."""
        return self.subtotal - self.credit


def compute_charge(price: Decimal, credit: Decimal = ZERO) -> Charge:
    """Price minus credit, plus PPN on that net amount (a price reduction, so the tax follows it)."""
    subtotal = quantize(price)
    credit = min(quantize(credit), subtotal)
    vat = compute_vat(subtotal - credit, SUBSCRIPTION_PPN_RATE, *SUBSCRIPTION_DPP)
    return Charge(subtotal=subtotal, tax_base=vat.dpp, tax=vat.tax, total=subtotal - credit + vat.tax, credit=credit)


# --- Proration -----------------------------------------------------------------------------


@dataclass(frozen=True)
class PaidPeriod:
    """A paid stretch of a plan and the value (excl. PPN) it represents."""

    start: date
    end: date  # inclusive
    value: Decimal

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1


@dataclass(frozen=True)
class Credit:
    amount: Decimal  # whole rupiah, excl. PPN
    days: int  # unused paid days after today

    @classmethod
    def none(cls) -> "Credit":
        return cls(ZERO, 0)


def unused_credit(periods: list[PaidPeriod], today: date) -> Credit:
    """Value of the paid days after ``today`` (today itself counts as used)."""
    amount, days = ZERO, 0
    for period in periods:
        first_unused = max(today + timedelta(days=1), period.start)
        unused = (period.end - first_unused).days + 1
        if unused > 0:
            amount += period.value * unused / period.days
            days += unused
    return Credit(round_down_rupiah(amount), days)


def period_days(start: date, period: "BillingPeriod") -> int:
    return (add_months(start, period.months) - start).days


@dataclass(frozen=True)
class SwitchQuote:
    charge: Charge
    extension_days: int = 0  # leftover credit converted into extra days on the new plan
    covered_until: date | None = None  # last day covered, if switching now needs no payment

    @property
    def needs_payment(self) -> bool:
        return self.charge.total > 0


def switch_quote(*, price: Decimal, credit: Credit, period: "BillingPeriod", starts_on: date) -> SwitchQuote:
    """What switching to a plan costs after crediting unused time on the current plan.

    If the credit covers the whole price, nothing is charged and the remainder
    becomes extra days at the new plan's daily rate (no balances, no refunds).
    """
    charge = compute_charge(price, credit.amount)
    leftover = credit.amount - charge.credit
    extension = 0
    if leftover > 0 and price > 0:
        daily_rate = Decimal(price) / period_days(starts_on, period)
        extension = int(leftover / daily_rate)
    covered_until = None
    if charge.total <= 0:
        covered_until = starts_on + timedelta(days=period_days(starts_on, period) + extension - 1)
    return SwitchQuote(charge=charge, extension_days=extension, covered_until=covered_until)


def add_months(day: date, months: int) -> date:
    """Calendar-aware month addition, clamping to the month's last day (31 Jan + 1 → 28/29 Feb)."""
    index = day.month - 1 + months
    year, month = day.year + index // 12, index % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def coverage_for_payment(
    *, paid_on: date, period: BillingPeriod, renewing_same_plan: bool, current_paid_until: date | None,
    extension_days: int = 0, share: Decimal = Decimal(1),
) -> tuple[date, date]:
    """First and last day a payment covers.

    Renewing the same plan before it lapses continues from the current end date,
    so no paid days are lost. Otherwise coverage starts on the payment date.
    ``extension_days`` adds converted leftover credit; ``share`` < 1 shortens the
    period proportionally when only part of the price was actually paid.
    """
    if renewing_same_plan and current_paid_until is not None and current_paid_until >= paid_on:
        start = current_paid_until + timedelta(days=1)
    else:
        start = paid_on
    days = period_days(start, period)
    if share < 1:
        days = max(1, int(days * share))
    return start, start + timedelta(days=days + extension_days - 1)
