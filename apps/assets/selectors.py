from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.db.models import QuerySet

from apps.core.money import ZERO
from apps.ledger.domain import AccountCategory
from apps.ledger.models import Account
from apps.ledger.selectors import account_balance, accounts_for

from .domain import ScheduledMonth
from .models import DepreciationPosting, FixedAsset


def assets_for(organization) -> QuerySet[FixedAsset]:
    return FixedAsset.objects.for_organization(organization).select_related(
        "asset_account", "accumulated_depreciation_account", "depreciation_expense_account", "disposal_entry"
    )


def fixed_asset_accounts(organization) -> QuerySet[Account]:
    return accounts_for(organization, active_only=True).filter(category=AccountCategory.FIXED_ASSET, is_contra=False)


def accumulated_depreciation_accounts(organization) -> QuerySet[Account]:
    return accounts_for(organization, active_only=True).filter(category=AccountCategory.FIXED_ASSET, is_contra=True)


def depreciation_expense_accounts(organization) -> QuerySet[Account]:
    return accounts_for(organization, active_only=True).filter(
        category__in=[AccountCategory.OPERATING_EXPENSE, AccountCategory.COST_OF_SALES], is_contra=False
    )


def gain_loss_accounts(organization) -> QuerySet[Account]:
    return accounts_for(organization, active_only=True).filter(
        category__in=[AccountCategory.OTHER_INCOME, AccountCategory.OTHER_EXPENSE], is_contra=False
    )


def posted_amounts(asset: FixedAsset) -> dict[tuple[int, int], DepreciationPosting]:
    return {(p.year, p.month): p for p in asset.postings.select_related("journal_entry")}


def accumulated_depreciation(asset: FixedAsset, *, through: date | None = None) -> Decimal:
    """Opening balance plus what the register posted, up to ``through``."""
    postings = asset.postings.all()
    if through is not None:
        postings = [p for p in postings if (p.year, p.month) <= (through.year, through.month)]
    return asset.opening_depreciation + sum((p.amount for p in postings), ZERO)


def months_due(asset: FixedAsset, through: date, posted: set[tuple[int, int]]) -> list[ScheduledMonth]:
    """Scheduled months that have ended by ``through`` and are neither posted nor in the opening balances."""
    if asset.status != FixedAsset.Status.ACTIVE:
        return []
    return [
        m for m in asset.schedule
        if m.period_end <= through and m.key not in posted and not asset.is_booked_elsewhere(m) and m.amount > 0
    ]


def due_depreciation(organization, through: date) -> dict[tuple[int, int], list[tuple[FixedAsset, ScheduledMonth]]]:
    """Unposted depreciation by month, oldest first."""
    posted = defaultdict(set)
    for asset_id, year, month in DepreciationPosting.objects.for_organization(organization).values_list("asset_id", "year", "month"):
        posted[asset_id].add((year, month))
    due = defaultdict(list)
    for asset in assets_for(organization).filter(status=FixedAsset.Status.ACTIVE, is_depreciable=True):
        for month in months_due(asset, through, posted[asset.pk]):
            due[month.key].append((asset, month))
    return dict(sorted(due.items()))


@dataclass(frozen=True)
class RegisterRow:
    asset: FixedAsset
    accumulated: Decimal

    @property
    def book_value(self) -> Decimal:
        return ZERO if self.asset.status == FixedAsset.Status.DISPOSED else self.asset.cost - self.accumulated


@dataclass(frozen=True)
class Reconciliation:
    """Register totals next to the ledger balance of the same account."""

    account: Account
    register: Decimal
    ledger: Decimal

    @property
    def difference(self) -> Decimal:
        return self.ledger - self.register


def register(organization) -> list[RegisterRow]:
    assets = list(assets_for(organization).prefetch_related("postings"))
    return [RegisterRow(asset, accumulated_depreciation(asset)) for asset in assets]


def reconcile(organization, rows: list[RegisterRow], as_of: date) -> list[Reconciliation]:
    """Cost of assets in use per asset account, and their accumulated depreciation per
    contra account, against the ledger. A difference means a purchase, sale or opening
    balance that isn't in the register (or the other way round)."""
    cost, accumulated = defaultdict(lambda: ZERO), defaultdict(lambda: ZERO)
    accounts = {}
    for row in rows:
        if row.asset.status != FixedAsset.Status.ACTIVE:
            continue
        accounts[row.asset.asset_account_id] = row.asset.asset_account
        cost[row.asset.asset_account_id] += row.asset.cost
        if row.asset.accumulated_depreciation_account_id:
            accounts[row.asset.accumulated_depreciation_account_id] = row.asset.accumulated_depreciation_account
            accumulated[row.asset.accumulated_depreciation_account_id] += row.accumulated
    totals = {**cost, **accumulated}
    return [
        Reconciliation(account, totals[pk], account_balance(organization, account, as_of=as_of))
        for pk, account in sorted(accounts.items(), key=lambda item: item[1].code)
    ]

