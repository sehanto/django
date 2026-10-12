from datetime import date
from decimal import Decimal

import pytest

from apps.billing.domain import (
    BillingPeriod,
    SubscriptionState,
    add_months,
    compute_charge,
    coverage_for_payment,
    subscription_state,
)

D = Decimal


@pytest.mark.parametrize(
    ("price", "dpp", "tax", "total"),
    [("99000", "90750", "10890", "109890"), ("249000", "228250", "27390", "276390"), ("990000", "907500", "108900", "1098900")],
)
def test_charge_adds_ppn_12_percent_on_dpp_11_12(price, dpp, tax, total):
    charge = compute_charge(D(price))
    assert (charge.subtotal, charge.tax_base, charge.tax, charge.total) == (D(price), D(dpp), D(tax), D(total))


@pytest.mark.parametrize(
    ("start", "months", "expected"),
    [(date(2026, 1, 31), 1, date(2026, 2, 28)), (date(2024, 1, 31), 1, date(2024, 2, 29)),
     (date(2026, 11, 15), 12, date(2027, 11, 15)), (date(2026, 12, 10), 1, date(2027, 1, 10))],
)
def test_add_months_clamps_to_month_end(start, months, expected):
    assert add_months(start, months) == expected


def test_new_plan_starts_on_payment_date():
    assert coverage_for_payment(
        paid_on=date(2026, 3, 10), period=BillingPeriod.MONTHLY, renewing_same_plan=False, current_paid_until=date(2026, 4, 1)
    ) == (date(2026, 3, 10), date(2026, 4, 9))


def test_early_renewal_continues_from_current_end():
    assert coverage_for_payment(
        paid_on=date(2026, 3, 25), period=BillingPeriod.YEARLY, renewing_same_plan=True, current_paid_until=date(2026, 3, 31)
    ) == (date(2026, 4, 1), date(2027, 3, 31))


def test_renewal_after_expiry_starts_on_payment_date():
    assert coverage_for_payment(
        paid_on=date(2026, 4, 5), period=BillingPeriod.MONTHLY, renewing_same_plan=True, current_paid_until=date(2026, 3, 31)
    ) == (date(2026, 4, 5), date(2026, 5, 4))


@pytest.mark.parametrize(
    ("today", "expected"),
    [(date(2026, 3, 1), SubscriptionState.ACTIVE), (date(2026, 3, 25), SubscriptionState.EXPIRING),
     (date(2026, 3, 31), SubscriptionState.EXPIRING), (date(2026, 4, 7), SubscriptionState.GRACE),
     (date(2026, 4, 8), SubscriptionState.LAPSED)],
)
def test_subscription_state_timeline(today, expected):
    assert subscription_state(
        is_free_plan=False, paid_until=date(2026, 3, 31), today=today, reminder_days=7, grace_days=7
    ) == expected


def test_free_plan_state():
    assert subscription_state(is_free_plan=True, paid_until=None, today=date(2026, 1, 1), reminder_days=7, grace_days=7) == SubscriptionState.FREE
