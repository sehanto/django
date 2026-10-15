"""Prepayments register: reads."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.db.models import QuerySet

from apps.core.money import ZERO
from apps.ledger.domain import AccountCategory
from apps.ledger.models import Account
from apps.ledger.selectors import accounts_for

from .domain import DeferralKind, ScheduledMonth
from .models import Deferral, DeferralPosting

# Where an amount waits (SAK EMKM 14.4a, 14.13) and where it is recognised, per kind.
BALANCE_CATEGORIES = {
    DeferralKind.PREPAID_EXPENSE: (AccountCategory.OTHER_CURRENT_ASSET,),
    DeferralKind.DEFERRED_REVENUE: (AccountCategory.CURRENT_LIABILITY, AccountCategory.LONG_TERM_LIABILITY),
}
RECOGNITION_CATEGORIES = {
    DeferralKind.PREPAID_EXPENSE: (AccountCategory.OPERATING_EXPENSE, AccountCategory.COST_OF_SALES, AccountCategory.OTHER_EXPENSE),
    DeferralKind.DEFERRED_REVENUE: (AccountCategory.REVENUE, AccountCategory.OTHER_INCOME),
}


def balance_accounts(organization, kind: str) -> QuerySet[Account]:
    """Accounts automated postings don't own, e.g. Biaya Dibayar di Muka or Pendapatan Diterima di Muka."""
    return accounts_for(organization, active_only=True).filter(
        category__in=BALANCE_CATEGORIES[kind], system_role__isnull=True, is_contra=False,
    )


def recognition_accounts(organization, kind: str) -> QuerySet[Account]:
    return accounts_for(organization, active_only=True).filter(category__in=RECOGNITION_CATEGORIES[kind], is_contra=False)


def deferrals_for(organization) -> QuerySet[Deferral]:
    return Deferral.objects.for_organization(organization).select_related("balance_account", "recognition_account", "opening_entry")


def posted_amounts(deferral: Deferral) -> dict[tuple[int, int], Decimal]:
    return {(p.year, p.month): p.amount for p in deferral.postings.all()}


def due_recognition(organization, through: date) -> dict[tuple[int, int], list[tuple[Deferral, ScheduledMonth]]]:
    """Months that have ended by ``through`` and aren't recognised yet, oldest first."""
    due = defaultdict(list)
    active = deferrals_for(organization).filter(status=Deferral.Status.ACTIVE).prefetch_related("postings").order_by("number")
    for deferral in active:
        posted = posted_amounts(deferral)
        for month in deferral.schedule:
            if month.period_end <= through and month.key not in posted:
                due[month.key].append((deferral, month))
    return dict(sorted(due.items()))


@dataclass(frozen=True)
class RegisterRow:
    deferral: Deferral
    recognised: Decimal

    @property
    def remaining(self) -> Decimal:
        return self.deferral.amount - self.recognised if self.deferral.status == Deferral.Status.ACTIVE else ZERO


def register(organization) -> list[RegisterRow]:
    deferrals = deferrals_for(organization).prefetch_related("postings")
    return [RegisterRow(d, sum((p.amount for p in d.postings.all()), ZERO)) for d in deferrals]


def postings_of(deferral: Deferral) -> QuerySet[DeferralPosting]:
    return deferral.postings.select_related("journal_entry")
