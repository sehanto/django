"""Calendar months, the tax period (masa pajak) of Indonesian returns."""

import calendar
from datetime import date


def month_start(year: int, month: int) -> date:
    return date(year, month, 1)


def month_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def add_months(year: int, month: int, count: int) -> tuple[int, int]:
    """The (year, month) ``count`` months after (or, if negative, before) the given one."""
    index = year * 12 + (month - 1) + count
    return index // 12, index % 12 + 1
