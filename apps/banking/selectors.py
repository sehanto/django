from datetime import date, timedelta
from decimal import Decimal

from django.db.models import QuerySet, Sum

from apps.core.money import ZERO
from apps.ledger.domain import JournalSource
from apps.ledger.models import JournalEntry, JournalLine
from apps.ledger.selectors import cash_accounts

from .domain import MATCH_DAYS, StatementLineStatus
from .models import StatementLine


def statement_lines(organization, account, *, status: str = "") -> QuerySet[StatementLine]:
    lines = StatementLine.objects.for_organization(organization).filter(account=account)
    if status:
        lines = lines.filter(status=status)
    return lines.select_related("journal_line__entry")


def bank_accounts(organization):
    return cash_accounts(organization)


def candidates(line: StatementLine, *, days: int = MATCH_DAYS) -> QuerySet[JournalLine]:
    """Posted lines on the bank account with the same amount, within ``days`` of the statement's
    date, not reconciled with another statement line yet: oldest first."""
    side = {"debit": line.amount} if line.amount > 0 else {"credit": -line.amount}
    return (
        JournalLine.objects.filter(
            entry__organization_id=line.organization_id, entry__status=JournalEntry.Status.POSTED, account_id=line.account_id,
            entry__date__gte=line.date - timedelta(days=days), entry__date__lte=line.date + timedelta(days=days),
            statement_line__isnull=True, **side,
        )
        .exclude(entry__source=JournalSource.OPENING)  # a balance brought over, not a movement on the statement
        .select_related("entry").order_by("entry__date", "id")
    )


def reconciliation(organization, account, as_of: date) -> dict:
    """The bank's balance (the last statement line up to ``as_of`` that has one) against the
    books, and what explains the difference: entries not on the statement yet, and statement lines
    not in the books yet."""
    last = (
        StatementLine.objects.for_organization(organization).filter(account=account, date__lte=as_of, balance__isnull=False)
        .order_by("-date", "-id").first()
    )
    posted = JournalLine.objects.filter(
        entry__organization=organization, entry__status=JournalEntry.Status.POSTED, account=account, entry__date__lte=as_of,
    )
    totals = posted.aggregate(debit=Sum("debit", default=ZERO), credit=Sum("credit", default=ZERO))
    books = totals["debit"] - totals["credit"]
    lines = StatementLine.objects.for_organization(organization).filter(account=account, date__lte=as_of)
    unrecorded = lines.filter(status=StatementLineStatus.UNMATCHED).aggregate(total=Sum("amount", default=ZERO))["total"]
    first = lines.order_by("date").values_list("date", flat=True).first()
    in_transit = Decimal(0)
    if first is not None:
        open_lines = posted.filter(entry__date__gte=first, statement_line__isnull=True)
        sums = open_lines.aggregate(debit=Sum("debit", default=ZERO), credit=Sum("credit", default=ZERO))
        in_transit = sums["debit"] - sums["credit"]
    return {
        "statement": last.balance if last is not None else None, "statement_date": last.date if last is not None else None,
        "books": books, "unrecorded": unrecorded, "in_transit": in_transit,
    }


def journal_lines_for(organization) -> QuerySet[JournalLine]:
    return JournalLine.objects.filter(entry__organization=organization).select_related("entry")


def unreconciled_counts(organization) -> dict[int, int]:
    from django.db.models import Count

    rows = (
        StatementLine.objects.for_organization(organization).filter(status=StatementLineStatus.UNMATCHED)
        .values("account_id").annotate(count=Count("id"))
    )
    return {row["account_id"]: row["count"] for row in rows}
