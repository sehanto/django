"""Depreciation rules for the fixed asset register (pure functions, no database access).

SAK EMKM measures fixed assets at cost less accumulated depreciation; land is not
depreciated. Depreciation is recorded monthly from the month of acquisition, so an
asset with a useful life of 48 months is fully depreciated after its 48th month:

* straight line: (cost - residual value) spread evenly over the useful life;
* declining balance: each year of the asset's life charges twice the straight-line
  rate on the book value at the start of that year (as in Indonesian tax practice,
  e.g. 50% a year over 4 years), and the last year charges what is left down to the
  residual value. A year's charge is spread evenly over its months.

Monthly amounts are whole rupiah, rounded on the running total so that they always
add up exactly to the depreciable amount.

A revised useful life, residual value or depreciation method is a change in accounting
estimate, applied prospectively: months before the revision keep their amounts, and from
the revision's month the book value at that point, less the new residual value, is
depreciated over the remaining useful life with the revision's method (for declining
balance, at twice the rate of the remaining life).
"""

import calendar
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.money import ZERO, quantize

MONTHS_PER_YEAR = 12
MAX_USEFUL_LIFE_MONTHS = 50 * MONTHS_PER_YEAR


class DepreciationMethod(models.TextChoices):
    """The depreciation methods SAK EMKM allows for fixed assets."""

    STRAIGHT_LINE = "straight_line", _("Straight line")
    DECLINING_BALANCE = "declining_balance", _("Declining balance")


class AssetStatus(models.TextChoices):
    ACTIVE = "active", _("In use")
    DISPOSED = "disposed", _("Disposed")


def month_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def add_months(year: int, month: int, count: int) -> tuple[int, int]:
    index = year * MONTHS_PER_YEAR + (month - 1) + count
    return index // MONTHS_PER_YEAR, index % MONTHS_PER_YEAR + 1


@dataclass(frozen=True)
class ScheduledMonth:
    year: int
    month: int
    amount: Decimal
    accumulated: Decimal  # after this month
    book_value: Decimal  # after this month

    @property
    def period_end(self) -> date:
        return month_end(self.year, self.month)

    @property
    def key(self) -> tuple[int, int]:
        return self.year, self.month


def _whole_rupiah(value: Decimal) -> Decimal:
    return quantize(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _spread(total: Decimal, months: int) -> list[Decimal]:
    """``total`` over ``months`` whole-rupiah amounts that add up exactly."""
    amounts, before = [], ZERO
    for index in range(1, months + 1):
        running = _whole_rupiah(total * index / months)
        amounts.append(running - before)
        before = running
    return amounts


def _yearly_charges(depreciable: Decimal, residual: Decimal, life_months: int, method: str) -> list[tuple[Decimal, int]]:
    """(charge, months) for each year of the asset's life; the last year may be shorter."""
    years, remaining = [], life_months
    while remaining > 0:
        years.append(min(MONTHS_PER_YEAR, remaining))
        remaining -= years[-1]
    if method == DepreciationMethod.STRAIGHT_LINE:
        return [(depreciable, life_months)]  # one even spread over the whole life
    rate = min(Decimal(2 * MONTHS_PER_YEAR) / Decimal(life_months), Decimal(1))
    charges, book_value = [], depreciable + residual
    for index, months in enumerate(years):
        left = book_value - residual
        charge = left if index == len(years) - 1 else min(_whole_rupiah(book_value * rate), left)
        charges.append((charge, months))
        book_value -= charge
    return charges


@dataclass(frozen=True)
class Estimate:
    """A revised useful life (total, counted from the month of acquisition), residual value
    and depreciation method, applied from ``starts`` (year, month)."""

    starts: tuple[int, int]
    useful_life_months: int
    residual_value: Decimal
    method: str


def months_between(start: tuple[int, int], end: tuple[int, int]) -> int:
    return (end[0] - start[0]) * MONTHS_PER_YEAR + (end[1] - start[1])


def _plain_schedule(cost: Decimal, residual_value: Decimal, life_months: int, method: str, start: tuple[int, int]) -> list[ScheduledMonth]:
    depreciable = quantize(cost) - quantize(residual_value)
    if depreciable <= 0 or life_months <= 0:
        return []
    schedule, accumulated, offset = [], ZERO, 0
    for charge, months in _yearly_charges(depreciable, quantize(residual_value), life_months, method):
        for amount in _spread(charge, months):
            year, month = add_months(*start, offset)
            accumulated += amount
            schedule.append(ScheduledMonth(year, month, amount, accumulated, quantize(cost) - accumulated))
            offset += 1
    return schedule


def accumulated_before(schedule: Sequence[ScheduledMonth], key: tuple[int, int]) -> Decimal:
    earlier = [m for m in schedule if m.key < key]
    return earlier[-1].accumulated if earlier else ZERO


def depreciation_schedule(
    *, cost: Decimal, residual_value: Decimal, useful_life_months: int, method: str, acquired_on: date,
    revisions: Sequence[Estimate] = (),
) -> list[ScheduledMonth]:
    """Month-by-month depreciation from the month of acquisition to the end of the useful life,
    with each revision applied from its month on (in order; a later one for the same month wins)."""
    acquired = (acquired_on.year, acquired_on.month)
    schedule = _plain_schedule(cost, residual_value, useful_life_months, method, acquired)
    for revision in sorted(revisions, key=lambda r: r.starts):  # stable: same-month revisions keep their order
        before = accumulated_before(schedule, revision.starts)
        remaining = revision.useful_life_months - months_between(acquired, revision.starts)
        tail = _plain_schedule(quantize(cost) - before, revision.residual_value, remaining, revision.method, revision.starts)
        schedule = [m for m in schedule if m.key < revision.starts] + [
            ScheduledMonth(m.year, m.month, m.amount, before + m.accumulated, m.book_value) for m in tail
        ]
    return schedule
