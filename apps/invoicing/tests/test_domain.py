from decimal import Decimal

import pytest

from apps.core.exceptions import ValidationFailed
from apps.invoicing.domain import (
    LineSpec,
    TaxSpec,
    aging_bucket,
    allocate,
    compute_invoice_totals,
    payment_state,
)

D = Decimal
PPN = TaxSpec(key=1, rate=D("12"), dpp_numerator=11, dpp_denominator=12)


def test_line_net_amount_with_discount():
    assert LineSpec(D("3"), D("10000"), D("10")).net_amount == D("27000.00")


@pytest.mark.parametrize(
    "line",
    [LineSpec(D("0"), D("1")), LineSpec(D("1"), D("-1")), LineSpec(D("1"), D("1"), D("101"))],
)
def test_invalid_lines(line):
    with pytest.raises(ValidationFailed):
        line.net_amount


def test_tax_is_computed_on_aggregated_base_per_rate():
    totals = compute_invoice_totals(
        [LineSpec(D("1"), D("333333"), tax=PPN), LineSpec(D("2"), D("333333"), tax=PPN), LineSpec(D("1"), D("50000"))]
    )
    assert totals.subtotal == D("1049999.00")
    assert len(totals.tax_groups) == 1
    assert totals.tax_groups[0].base == D("999999.00")
    assert totals.tax_total == D("109999.00")  # 999999 * 11/12 * 12% = 109999.89 → rounded down
    assert totals.total == D("1159998.00")


def test_empty_invoice_rejected():
    with pytest.raises(ValidationFailed):
        compute_invoice_totals([])


def test_allocate_keeps_total_exact():
    shares = allocate(D("100.00"), [D("1"), D("1"), D("1")])
    assert shares == [D("33.33"), D("33.33"), D("33.34")]
    assert sum(shares) == D("100.00")


def test_payment_state():
    assert payment_state(D("100"), D("0")) == "issued"
    assert payment_state(D("100"), D("40")) == "partial"
    assert payment_state(D("100"), D("100")) == "paid"


@pytest.mark.parametrize(("days", "bucket"), [(-3, "current"), (0, "current"), (1, "d1_30"), (45, "d31_60"), (90, "d61_90"), (91, "d90_plus")])
def test_aging_bucket(days, bucket):
    assert aging_bucket(days) == bucket
