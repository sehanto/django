"""NPWP (Nomor Pokok Wajib Pajak) rules.

Since 2024 Indonesia uses a 16-digit NPWP (NIK for individuals, or the old
15-digit number prefixed with ``0``). The legacy 15-digit format is still
accepted for historical data.
"""

import re

from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.utils.translation import gettext_lazy as _

_NON_DIGITS = re.compile(r"\D")


def normalize_npwp(value: str) -> str:
    return _NON_DIGITS.sub("", value or "")


npwp_format_validator = RegexValidator(r"^\d{15}(\d)?$", _("NPWP must have 15 or 16 digits."))


def validate_npwp(value: str) -> str:
    digits = normalize_npwp(value)
    if len(digits) not in (15, 16):
        raise ValidationError(_("NPWP must have 15 or 16 digits."))
    return digits


def npwp16(value: str) -> str:
    """The 16-digit NPWP Coretax expects: a 15-digit NPWP is prefixed with 0."""
    digits = normalize_npwp(value)
    return "0" + digits if len(digits) == 15 else digits


# NITKU (Nomor Identitas Tempat Kegiatan Usaha): the 16-digit NPWP and six digits for the place
# of business, 000000 being the head office.
HEAD_OFFICE = "000000"
nitku_validator = RegexValidator(r"^\d{22}$", _("A NITKU has 22 digits: the 16-digit NPWP followed by 6 digits."))


def validate_nitku_of(nitku: str, npwp: str) -> None:
    """A NITKU belongs to the NPWP it starts with."""
    if nitku and (not npwp or nitku[:16] != npwp16(npwp)):
        raise ValidationError(_("A NITKU starts with the 16-digit NPWP of the same taxpayer. Enter the NPWP first."))


def format_npwp(value: str) -> str:
    """15 digit → ``01.234.567.8-901.000``; 16 digit → ``0123 4567 8901 2345``.

    The 16-digit NPWP (the NIK, or 0 + the old number) has no official punctuation; DJP
    shows it as plain digits. Groups of four keep it readable without inventing one.
    Anything else (an unexpected length) is shown as stored.
    """
    digits = normalize_npwp(value)
    if len(digits) == 15:
        return f"{digits[0:2]}.{digits[2:5]}.{digits[5:8]}.{digits[8]}-{digits[9:12]}.{digits[12:15]}"
    if len(digits) == 16:
        return " ".join(digits[i:i + 4] for i in range(0, 16, 4))
    return digits
