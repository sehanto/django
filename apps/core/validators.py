"""Reusable format validators for Indonesian business identifiers and common inputs.

Used on model fields so that forms and the Django admin enforce the same rules.
"""

from datetime import date, timedelta

from django.core.exceptions import ValidationError
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
# Numbers of documents typed in from elsewhere (PIB/PEB, bukti potong, BPSP, SKB, certificates of
# residence): letters, digits, spaces and the separators they use. Quotes, angle brackets, ';',
# '=' and control characters never belong in one, and would travel into Coretax files.
DOCUMENT_NUMBER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9 .,/()_:#&-]*$"
document_number_validator = RegexValidator(
    DOCUMENT_NUMBER_PATTERN, _("Use letters, digits, spaces and . , / ( ) _ : # & - only, starting with a letter or digit."),
)
# Identity document numbers and foreign tax IDs: passports, KITAS, a TIN abroad.
identifier_validator = RegexValidator(r"^[A-Za-z0-9./-]*$", _("Use letters, digits, dots, slashes and dashes only."))
tax_invoice_number_validator = RegexValidator(
    r"^[0-9][0-9.\-]{0,29}$", _("A tax invoice number contains only digits, '.' and '-'.")
)

# Transactions outside this window are almost certainly typos (e.g. year 0026 or 2206).
# Text that reaches a column or a hash is bounded: an email column holds 254 characters (RFC 5321),
# while Django's form EmailField accepts up to 320; a password is hashed whole.
EMAIL_MAX_LENGTH = 254
PASSWORD_MAX_LENGTH = 4096
SECRET_MAX_LENGTH = 4096  # access tokens of outside services, stored encrypted

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


def ensure_document_number(number: str, *, max_length: int, missing: str) -> str:
    """Service-level check of a document number recorded once (``missing``: the message when empty)."""
    number = number.strip()
    if not number or len(number) > max_length:
        raise ValidationFailed(missing)
    try:
        document_number_validator(number)
    except ValidationError as exc:
        raise ValidationFailed(exc.messages[0]) from None
    return number
