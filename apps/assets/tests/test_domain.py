from datetime import date
from decimal import Decimal

import pytest

from apps.assets.domain import DepreciationMethod, Estimate, depreciation_schedule
from apps.core.periods import add_months

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


# --- Changes in estimate (prospective) ---------------------------------------------


def revised(cost, months, *revisions, method=DepreciationMethod.STRAIGHT_LINE, residual="0"):
    return depreciation_schedule(
        cost=D(cost), residual_value=D(residual), useful_life_months=months, method=method, acquired_on=date(2026, 1, 15),
        revisions=[Estimate(r[0], r[1], D(r[2]), r[3] if len(r) > 3 else method) for r in revisions],
    )


def test_a_longer_life_spreads_the_remaining_book_value_over_the_remaining_months():
    months = revised("4800000", 48, ((2027, 1), 60, "0"))  # after 12 months of 100.000
    assert len(months) == 60
    assert {m.amount for m in months[:12]} == {D("100000")}  # the past stays
    assert {m.amount for m in months[12:]} == {D("75000")}  # 3.600.000 over 48 months
    assert months[11].accumulated == D("1200000") and months[-1].book_value == 0


def test_a_new_residual_value_is_kept_at_the_end():
    months = revised("4800000", 48, ((2027, 1), 48, "600000"))
    assert months[-1].book_value == D("600000") and len(months) == 48
    assert sum(m.amount for m in months[12:]) == D("3000000")


def test_declining_balance_restarts_on_the_remaining_book_value():
    months = revised("10000000", 48, ((2027, 1), 36, "1000000"), method=DepreciationMethod.DECLINING_BALANCE)
    assert sum(m.amount for m in months[:12]) == D("5000000")  # year one as before
    assert len(months) == 36 and months[-1].book_value == D("1000000")
    # 24 months left: twice the straight-line rate is 100%, capped at what's left above the residual value.
    assert sum(m.amount for m in months[12:24]) == D("4000000")


def test_the_last_revision_for_a_month_wins():
    months = revised("4800000", 48, ((2027, 1), 60, "0"), ((2027, 1), 24, "0"))
    assert len(months) == 24 and months[-1].book_value == 0


def test_revisions_apply_in_order():
    months = revised("4800000", 48, ((2027, 1), 60, "0"), ((2028, 1), 36, "0"))
    assert len(months) == 36 and months[-1].book_value == 0
    assert months[23].accumulated == D("1200000") + 12 * D("75000")


def test_switching_to_declining_balance_applies_to_the_remaining_book_value():
    months = revised("4800000", 48, ((2027, 1), 48, "0", DepreciationMethod.DECLINING_BALANCE))
    assert {m.amount for m in months[:12]} == {D("100000")}  # straight line so far
    # 3.600.000 left over 36 months: twice the straight-line rate is 2/3 a year.
    assert sum(m.amount for m in months[12:24]) == D("2400000")
    assert sum(m.amount for m in months[24:36]) == D("800000")
    assert sum(m.amount for m in months[36:]) == D("400000") and months[-1].book_value == 0


def test_switching_back_to_straight_line_spreads_what_is_left_evenly():
    months = revised("10000000", 48, ((2027, 1), 48, "0", DepreciationMethod.STRAIGHT_LINE), method=DepreciationMethod.DECLINING_BALANCE)
    assert sum(m.amount for m in months[:12]) == D("5000000")
    assert {m.amount for m in months[12:]} <= {D("138888"), D("138889")}  # 5.000.000 over 36 months
    assert months[-1].book_value == 0
