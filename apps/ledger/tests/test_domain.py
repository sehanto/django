from datetime import date
from decimal import Decimal

import pytest

from apps.core.exceptions import PeriodLocked, ValidationFailed
from apps.ledger.domain import (
    AccountCategory,
    LineAmount,
    NormalBalance,
    ensure_period_open,
    normal_balance_for,
    signed_balance,
    validate_lines,
)

D = Decimal


def test_balanced_entry_returns_totals():
    assert validate_lines([LineAmount(debit=D("100")), LineAmount(credit=D("100"))]) == (D("100"), D("100"))


@pytest.mark.parametrize(
    "lines",
    [
        [LineAmount(debit=D("100"))],  # single line
        [LineAmount(debit=D("100")), LineAmount(credit=D("90"))],  # unbalanced
        [LineAmount(debit=D("100"), credit=D("100")), LineAmount(credit=D("0"))],  # two-sided line
        [LineAmount(debit=D("-5")), LineAmount(credit=D("-5"))],  # negative
        [LineAmount(), LineAmount()],  # empty lines
    ],
)
def test_invalid_entries_are_rejected(lines):
    with pytest.raises(ValidationFailed):
        validate_lines(lines)


def test_normal_balances_including_contra_accounts():
    assert normal_balance_for(AccountCategory.CASH_BANK, is_contra=False) == NormalBalance.DEBIT
    assert normal_balance_for(AccountCategory.FIXED_ASSET, is_contra=True) == NormalBalance.CREDIT
    assert normal_balance_for(AccountCategory.REVENUE, is_contra=False) == NormalBalance.CREDIT
    assert normal_balance_for(AccountCategory.EQUITY, is_contra=True) == NormalBalance.DEBIT


def test_signed_balance():
    assert signed_balance(D("100"), D("30"), NormalBalance.DEBIT) == D("70")
    assert signed_balance(D("100"), D("30"), NormalBalance.CREDIT) == D("-70")


def test_period_lock():
    ensure_period_open(date(2026, 2, 1), None)
    ensure_period_open(date(2026, 2, 1), date(2026, 1, 31))
    with pytest.raises(PeriodLocked):
        ensure_period_open(date(2026, 1, 31), date(2026, 1, 31))
