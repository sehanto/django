"""Reusable format validators for Indonesian business identifiers and common inputs.

Used on model fields so that forms and the Django admin enforce the same rules.
"""

from datetime import date, timedelta

from django.core.validators import RegexValidator
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .exceptions import ValidationFailed

phone_validator = RegexValidator(
    r"^\+?\(?[0-9][0-9 ()\-.]{5,24}$",
    _("Enter a valid phone number (digits, spaces, +, -, parentheses)."),
)
code_validator = RegexValidator(
    r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,49}$",
    _("Use letters, digits, '.', '-', '_' or '/' only, starting with a letter or digit."),
)
nib_validator = RegexValidator(r"^\d{13}$", _("NIB must be exactly 13 digits."))
ntpn_validator = RegexValidator(r"^[A-Z0-9]{16}$", _("NTPN must be 16 letters or digits."))
tax_invoice_number_validator = RegexValidator(
    r"^[0-9][0-9.\-]{0,29}$", _("A tax invoice number contains only digits, '.' and '-'.")
)

# Transactions outside this window are almost certainly typos (e.g. year 0026 or 2206).
EARLIEST_BUSINESS_DATE = date(2000, 1, 1)
MAX_DAYS_AHEAD = 366


def latest_business_date() -> date:
    return timezone.localdate() + timedelta(days=MAX_DAYS_AHEAD)


def ensure_business_date(value: date) -> None:
    """Service-level guard for dates that will be posted to the books."""
    if not EARLIEST_BUSINESS_DATE <= value <= latest_business_date():
        raise ValidationFailed(
            _("The date %(date)s is outside the allowed range (%(start)s to %(end)s).") % {
                "date": value.strftime("%d-%m-%Y"),
                "start": EARLIEST_BUSINESS_DATE.strftime("%d-%m-%Y"),
                "end": latest_business_date().strftime("%d-%m-%Y"),
            }
        )
