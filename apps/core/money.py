"""Money helpers for Indonesian Rupiah (IDR).

Amounts are stored as ``Decimal`` with two decimal places. Indonesian tax
regulations round tax amounts *down* to whole rupiah, so dedicated helpers are
provided for that.
"""

from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal, InvalidOperation

ZERO = Decimal("0.00")
CENT = Decimal("0.01")
RUPIAH = Decimal("1")

MONEY_MAX_DIGITS = 18
MONEY_DECIMAL_PLACES = 2


def to_decimal(value) -> Decimal:
    if value is None or value == "":
        return ZERO
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"Nilai uang tidak valid: {value!r}") from exc


def quantize(value) -> Decimal:
    """Round to two decimal places (half-up), the precision we store."""
    return to_decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def round_down_rupiah(value) -> Decimal:
    """Truncate to whole rupiah, as required for PPN/PPh amounts."""
    return to_decimal(value).quantize(RUPIAH, rounding=ROUND_DOWN).quantize(CENT)


def format_rupiah(value, with_symbol: bool = True) -> str:
    """Format using Indonesian conventions: ``Rp 1.234.567,50``.

    Fractions are omitted when the amount is a whole number.
    """
    amount = quantize(value)
    negative = amount < 0
    amount = abs(amount)
    integer_part, fraction = f"{amount:.2f}".split(".")
    grouped = f"{int(integer_part):,}".replace(",", ".")
    text = grouped if fraction == "00" else f"{grouped},{fraction}"
    if with_symbol:
        text = f"Rp {text}"
    return f"-{text}" if negative else text
