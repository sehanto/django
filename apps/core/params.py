"""Typed, whitelisted access to query-string and POST parameters.

Views must not pass raw request parameters to the ORM: an unknown filter
value, a NUL byte or a non-numeric ID would otherwise surface as a 500.
"""

from django.utils import timezone

from .sanitizers import sanitize_text
from .validators import EARLIEST_BUSINESS_DATE

MAX_SEARCH_LENGTH = 100


def text_param(params, name: str, *, max_length: int = MAX_SEARCH_LENGTH) -> str:
    return sanitize_text(str(params.get(name, "")))[:max_length]


def choice_param(params, name: str, allowed, *, default: str = "") -> str:
    value = params.get(name, default)
    return value if value in set(allowed) else default


def int_param(params, name: str, *, default=None, minimum: int | None = None, maximum: int | None = None):
    try:
        value = int(params.get(name, ""))
    except (TypeError, ValueError):
        return default
    if (minimum is not None and value < minimum) or (maximum is not None and value > maximum):
        return default
    return value


def year_param(params, name: str = "year") -> int:
    this_year = timezone.localdate().year
    return int_param(params, name, default=this_year, minimum=EARLIEST_BUSINESS_DATE.year, maximum=this_year + 1)


def year_choices() -> range:
    """Years offered in year pickers (newest first), consistent with ``year_param``."""
    this_year = timezone.localdate().year
    return range(this_year + 1, max(EARLIEST_BUSINESS_DATE.year, this_year - 6), -1)
