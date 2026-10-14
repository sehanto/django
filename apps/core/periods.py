"""Calendar months, the tax period (masa pajak) of Indonesian returns."""

import calendar
from datetime import date


def month_start(year: int, month: int) -> date:
    return date(year, month, 1)


def month_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])
