"""Amounts paid or received in advance, spread straight-line over the months they cover
(pure functions, no database access).

SAK EMKM expenses rent straight-line over the lease (14.13) and recognises rent and royalty
income straight-line over the contract (14.7); generally, income and expenses belong to the
period they arise in (accrual basis, 2.36), not to the month of payment. Until then an amount
paid in advance is an asset (prepaid expense) and an amount received in advance a liability
(revenue received in advance, 14.4a).

Monthly amounts are equal to the cent; the last month takes what rounding left over, so the
months add up exactly to the amount.
"""

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_DOWN, Decimal

from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.money import ZERO
from apps.core.periods import add_months, month_end

MAX_TERM_MONTHS = 10 * 12


class DeferralKind(models.TextChoices):
    PREPAID_EXPENSE = "prepaid_expense", _("Expense paid in advance")
    DEFERRED_REVENUE = "deferred_revenue", _("Revenue received in advance")


class DeferralStatus(models.TextChoices):
    ACTIVE = "active", _("Active")
    VOID = "void", _("Void")


@dataclass(frozen=True)
class ScheduledMonth:
    year: int
    month: int
    amount: Decimal
    recognised: Decimal  # in total, after this month

    @property
    def period_end(self) -> date:
        return month_end(self.year, self.month)

    @property
    def key(self) -> tuple[int, int]:
        return (self.year, self.month)


def straight_line(amount: Decimal, months: int, first: tuple[int, int]) -> list[ScheduledMonth]:
    """``amount`` spread evenly over ``months`` months from ``first`` (year, month)."""
    if months < 1 or amount <= 0:
        return []
    share = (amount / months).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
    schedule, recognised = [], ZERO
    for offset in range(months):
        this = amount - recognised if offset == months - 1 else share
        recognised += this
        schedule.append(ScheduledMonth(*add_months(*first, offset), amount=this, recognised=recognised))
    return schedule
