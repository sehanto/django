"""NPWP (Nomor Pokok Wajib Pajak) rules.

Since 2024 Indonesia uses a 16-digit NPWP (NIK for individuals, or the old
15-digit number prefixed with ``0``). The legacy 15-digit format is still
accepted for historical data.
"""

import re

from django.core.exceptions import ValidationError

_NON_DIGITS = re.compile(r"\D")


def normalize_npwp(value: str) -> str:
    return _NON_DIGITS.sub("", value or "")


def validate_npwp(value: str) -> str:
    digits = normalize_npwp(value)
    if len(digits) not in (15, 16):
        raise ValidationError("NPWP harus terdiri dari 15 atau 16 digit.")
    return digits


def format_npwp(value: str) -> str:
    """15 digit → ``01.234.567.8-901.000``; 16 digit is shown as-is."""
    digits = normalize_npwp(value)
    if len(digits) == 15:
        return f"{digits[0:2]}.{digits[2:5]}.{digits[5:8]}.{digits[8]}-{digits[9:12]}.{digits[12:15]}"
    return digits
