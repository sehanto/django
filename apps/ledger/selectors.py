"""Read-side queries for the general ledger. All results are tenant-scoped."""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from django.db.models import Q, QuerySet, Sum

from apps.core.money import ZERO

from .domain import AccountCategory, signed_balance
from .models import Account, JournalEntry, JournalLine


def accounts_for(organization, *, active_only: bool = False) -> QuerySet[Account]:
    qs = Account.objects.for_organization(organization)
    return qs.filter(is_active=True) if active_only else qs


def cash_accounts(organization) -> QuerySet[Account]:
    return accounts_for(organization, active_only=True).filter(category=AccountCategory.CASH_BANK)


def journal_entries_for(organization) -> QuerySet[JournalEntry]:
    return JournalEntry.objects.for_organization(organization).prefetch_related("lines__account")


def posted_lines(organization, *, date_from: date | None = None, date_to: date | None = None) -> QuerySet[JournalLine]:
    qs = JournalLine.objects.filter(
        entry__organization=organization, entry__status=JournalEntry.Status.POSTED
    )
    if date_from:
        qs = qs.filter(entry__date__gte=date_from)
    if date_to:
        qs = qs.filter(entry__date__lte=date_to)
    return qs


@dataclass
class AccountBalance:
    account: Account
    debit: Decimal = ZERO
    credit: Decimal = ZERO

    @property
    def balance(self) -> Decimal:
        """Positive when the account carries its normal balance."""
        return signed_balance(self.debit, self.credit, self.account.normal_balance)

    @property
    def net_debit(self) -> Decimal:
        return self.debit - self.credit


def account_balances(
    organization, *, date_from: date | None = None, date_to: date | None = None, include_zero: bool = False
) -> list[AccountBalance]:
    totals = {
        row["account_id"]: row
        for row in posted_lines(organization, date_from=date_from, date_to=date_to)
        .values("account_id")
        .annotate(debit=Sum("debit"), credit=Sum("credit"))
    }
    balances = []
    for account in accounts_for(organization):
        row = totals.get(account.pk)
        if row is None and not include_zero:
            continue
        balances.append(
            AccountBalance(
                account=account,
                debit=row["debit"] if row else ZERO,
                credit=row["credit"] if row else ZERO,
            )
        )
    return balances


def account_balance(organization, account: Account, *, as_of: date | None = None) -> Decimal:
    totals = posted_lines(organization, date_to=as_of).filter(account=account).aggregate(
        debit=Sum("debit", default=ZERO), credit=Sum("credit", default=ZERO)
    )
    return signed_balance(totals["debit"], totals["credit"], account.normal_balance)


@dataclass
class LedgerRow:
    date: date
    entry: JournalEntry
    description: str
    debit: Decimal
    credit: Decimal
    balance: Decimal


@dataclass
class AccountLedger:
    account: Account
    opening_balance: Decimal
    rows: list[LedgerRow] = field(default_factory=list)

    @property
    def closing_balance(self) -> Decimal:
        return self.rows[-1].balance if self.rows else self.opening_balance

    @property
    def total_debit(self) -> Decimal:
        return sum((r.debit for r in self.rows), ZERO)

    @property
    def total_credit(self) -> Decimal:
        return sum((r.credit for r in self.rows), ZERO)


def account_ledger(organization, account: Account, *, date_from: date, date_to: date) -> AccountLedger:
    opening = posted_lines(organization).filter(account=account, entry__date__lt=date_from).aggregate(
        debit=Sum("debit", default=ZERO), credit=Sum("credit", default=ZERO)
    )
    normal = account.normal_balance
    running = signed_balance(opening["debit"], opening["credit"], normal)
    ledger = AccountLedger(account=account, opening_balance=running)
    lines = (
        posted_lines(organization, date_from=date_from, date_to=date_to)
        .filter(account=account)
        .select_related("entry")
        .order_by("entry__date", "entry_id", "id")
    )
    for line in lines:
        running += signed_balance(line.debit, line.credit, normal)
        ledger.rows.append(
            LedgerRow(
                date=line.entry.date,
                entry=line.entry,
                description=line.description or line.entry.description,
                debit=line.debit,
                credit=line.credit,
                balance=running,
            )
        )
    return ledger


def search_journal_entries(organization, *, query: str = "", status: str = "", source: str = "") -> QuerySet[JournalEntry]:
    qs = journal_entries_for(organization)
    if query:
        qs = qs.filter(Q(number__icontains=query) | Q(description__icontains=query) | Q(reference__icontains=query))
    if status:
        qs = qs.filter(status=status)
    if source:
        qs = qs.filter(source=source)
    return qs


def revenue_accounts(organization) -> QuerySet[Account]:
    return accounts_for(organization, active_only=True).filter(
        category__in=[AccountCategory.REVENUE, AccountCategory.OTHER_INCOME]
    )


def purchase_accounts(organization) -> QuerySet[Account]:
    """Accounts a purchase line may debit: costs, expenses and non-cash assets."""
    return accounts_for(organization, active_only=True).filter(
        category__in=[
            AccountCategory.COST_OF_SALES,
            AccountCategory.OPERATING_EXPENSE,
            AccountCategory.OTHER_EXPENSE,
            AccountCategory.INVENTORY,
            AccountCategory.OTHER_CURRENT_ASSET,
            AccountCategory.FIXED_ASSET,
        ]
    )
