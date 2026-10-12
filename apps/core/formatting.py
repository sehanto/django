"""Display formatting that follows the active interface language.

The currency is always rupiah, but digit grouping follows the reader's locale:
``Rp 1.234.567,50`` in Indonesian, ``Rp 1,234,567.50`` in English.
"""

from django.utils.formats import get_format

from .money import format_rupiah


def localized_rupiah(value, with_symbol: bool = True) -> str:
    return format_rupiah(
        value,
        with_symbol,
        thousand_separator=get_format("THOUSAND_SEPARATOR"),
        decimal_separator=get_format("DECIMAL_SEPARATOR"),
    )
