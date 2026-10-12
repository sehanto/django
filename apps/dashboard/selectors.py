from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.db.models import Sum
from django.db.models.functions import TruncMonth

from apps.core.money import ZERO
from apps.ledger.domain import AccountType, categories_of
from apps.ledger.selectors import account_balance, cash_accounts, posted_lines


@dataclass
class MonthlyResult:
    month: date
    revenue: Decimal
    expense: Decimal

    @property
    def profit(self) -> Decimal:
        return self.revenue - self.expense


def _add_months(day: date, months: int) -> date:
    index = day.year * 12 + day.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


def monthly_results(organization, *, until: date, months: int = 6) -> list[MonthlyResult]:
    first = _add_months(until.replace(day=1), -(months - 1))
    revenue_categories = categories_of(AccountType.REVENUE)
    expense_categories = categories_of(AccountType.EXPENSE)
    rows = (
        posted_lines(organization, date_from=first, date_to=until)
        .filter(account__category__in=revenue_categories + expense_categories)
        .annotate(month=TruncMonth("entry__date"))
        .values("month", "account__category")
        .annotate(debit=Sum("debit"), credit=Sum("credit"))
    )
    buckets = {_add_months(first, i): MonthlyResult(_add_months(first, i), ZERO, ZERO) for i in range(months)}
    for row in rows:
        bucket = buckets[row["month"]]
        if row["account__category"] in revenue_categories:
            bucket.revenue += row["credit"] - row["debit"]
        else:
            bucket.expense += row["debit"] - row["credit"]
    return list(buckets.values())


def cash_position(organization, *, as_of: date) -> list[tuple]:
    return [(account, account_balance(organization, account, as_of=as_of)) for account in cash_accounts(organization)]
