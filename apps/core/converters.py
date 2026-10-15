"""URL segments for tax periods: an unknown year or month is a 404 before any view runs."""

from .validators import EARLIEST_BUSINESS_DATE, latest_business_date


class YearConverter:
    """A business year (``EARLIEST_BUSINESS_DATE`` to the latest allowed business date)."""

    regex = "[0-9]{4}"

    def to_python(self, value: str) -> int:
        year = int(value)
        if not EARLIEST_BUSINESS_DATE.year <= year <= latest_business_date().year:
            raise ValueError(value)  # no match: 404
        return year

    def to_url(self, value) -> str:
        return str(value)


class MonthConverter:
    """A calendar month, 1 to 12."""

    regex = "0?[1-9]|1[0-2]"

    def to_python(self, value: str) -> int:
        return int(value)

    def to_url(self, value) -> str:
        return str(int(value))
