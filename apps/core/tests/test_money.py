from decimal import Decimal

import pytest

from apps.core.money import format_rupiah, quantize, round_down_rupiah


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Decimal("1234567"), "Rp 1.234.567"),
        (Decimal("1234567.5"), "Rp 1.234.567,50"),
        (Decimal("0"), "Rp 0"),
        (Decimal("-2500"), "-Rp 2.500"),
    ],
)
def test_format_rupiah(value, expected):
    assert format_rupiah(value) == expected


def test_format_without_symbol():
    assert format_rupiah(1000, with_symbol=False) == "1.000"


def test_quantize_half_up():
    assert quantize("10.005") == Decimal("10.01")


def test_tax_rounds_down_to_whole_rupiah():
    assert round_down_rupiah(Decimal("10999.99")) == Decimal("10999.00")
