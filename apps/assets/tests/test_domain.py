from datetime import date
from decimal import Decimal

import pytest

from apps.assets.domain import DepreciationMethod, add_months, depreciation_schedule

D = Decimal


def schedule(cost, months, method=DepreciationMethod.STRAIGHT_LINE, residual="0", acquired_on=date(2026, 1, 15)):
    return depreciation_schedule(cost=D(cost), residual_value=D(residual), useful_life_months=months, method=method, acquired_on=acquired_on)


def test_straight_line_spreads_whole_rupiah_that_add_up_exactly():
    months = schedule("28000000", 48)
    assert len(months) == 48
    assert (months[0].year, months[0].month) == (2026, 1)  # from the month of acquisition
    assert (months[-1].year, months[-1].month) == (2029, 12)
    assert {m.amount for m in months} == {D("583333"), D("583334")}
    assert sum(m.amount for m in months) == D("28000000")
    assert months[-1].book_value == 0 and months[11].accumulated == D("7000000")


def test_straight_line_stops_at_the_residual_value():
    months = schedule("10000000", 24, residual="1000000")
    assert months[-1].book_value == D("1000000")
    assert sum(m.amount for m in months) == D("9000000")


def test_declining_balance_charges_double_the_straight_line_rate_on_book_value():
    months = schedule("10000000", 48, DepreciationMethod.DECLINING_BALANCE)
    yearly = [sum(m.amount for m in months[i:i + 12]) for i in range(0, 48, 12)]
    assert yearly == [D("5000000"), D("2500000"), D("1250000"), D("1250000")]  # 50% a year; the last year takes the rest
    assert months[-1].book_value == 0


def test_declining_balance_never_goes_below_the_residual_value():
    months = schedule("10000000", 30, DepreciationMethod.DECLINING_BALANCE, residual="2000000")
    assert len(months) == 30 and months[-1].book_value == D("2000000")
    assert all(m.amount >= 0 for m in months)


@pytest.mark.parametrize("cost, residual, months", [("1000", "1000", 12), ("1000", "0", 0)])
def test_nothing_to_depreciate(cost, residual, months):
    assert schedule(cost, months, residual=residual) == []


def test_months_roll_over_the_year():
    assert add_months(2026, 12, 1) == (2027, 1)
    assert add_months(2026, 1, -1) == (2025, 12)
    months = schedule("1200", 3, acquired_on=date(2026, 11, 30))
    assert [m.period_end for m in months] == [date(2026, 11, 30), date(2026, 12, 31), date(2027, 1, 31)]
