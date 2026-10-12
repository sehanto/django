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

from apps.core.money import quantize
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


class SubscriptionState(models.TextChoices):
    FREE = "free", _("Free plan")
    ACTIVE = "active", _("Active")
    EXPIRING = "expiring", _("Expiring soon")
    GRACE = "grace", _("Expired (grace period)")
    LAPSED = "lapsed", _("Lapsed")


@dataclass(frozen=True)
class Charge:
    subtotal: Decimal  # plan price, excluding PPN
    tax_base: Decimal  # DPP
    tax: Decimal
    total: Decimal


def compute_charge(price: Decimal) -> Charge:
    vat = compute_vat(price, SUBSCRIPTION_PPN_RATE, *SUBSCRIPTION_DPP)
    subtotal = quantize(price)
    return Charge(subtotal=subtotal, tax_base=vat.dpp, tax=vat.tax, total=subtotal + vat.tax)


def add_months(day: date, months: int) -> date:
    """Calendar-aware month addition, clamping to the month's last day (31 Jan + 1 → 28/29 Feb)."""
    index = day.month - 1 + months
    year, month = day.year + index // 12, index % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def coverage_for_payment(
    *, paid_on: date, period: BillingPeriod, renewing_same_plan: bool, current_paid_until: date | None
) -> tuple[date, date]:
    """First and last day a payment covers.

    Renewing the same plan before it lapses continues from the current end date,
    so no paid days are lost. Otherwise coverage starts on the payment date.
    """
    if renewing_same_plan and current_paid_until is not None and current_paid_until >= paid_on:
        start = current_paid_until + timedelta(days=1)
    else:
        start = paid_on
    return start, add_months(start, period.months) - timedelta(days=1)


def subscription_state(
    *, is_free_plan: bool, paid_until: date | None, today: date, reminder_days: int, grace_days: int
) -> SubscriptionState:
    if is_free_plan or paid_until is None:
        return SubscriptionState.FREE
    if today <= paid_until - timedelta(days=reminder_days):
        return SubscriptionState.ACTIVE
    if today <= paid_until:
        return SubscriptionState.EXPIRING
    if today <= paid_until + timedelta(days=grace_days):
        return SubscriptionState.GRACE
    return SubscriptionState.LAPSED
