from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.db.models import QuerySet

from apps.core.money import ZERO
from apps.ledger.domain import AccountCategory
from apps.ledger.models import Account
from apps.ledger.selectors import account_balance, accounts_for

from .domain import Stock, replay
from .models import StockMovement


def tracked_items(organization) -> QuerySet:
    from apps.invoicing.models import Item  # invoicing depends on inventory

    return Item.objects.for_organization(organization).filter(track_inventory=True).select_related("inventory_account", "cogs_account")


def inventory_accounts(organization) -> QuerySet[Account]:
    return accounts_for(organization, active_only=True).filter(category=AccountCategory.INVENTORY, is_contra=False)


def cost_of_sales_accounts(organization) -> QuerySet[Account]:
    return accounts_for(organization, active_only=True).filter(category=AccountCategory.COST_OF_SALES, is_contra=False)


def adjustment_accounts(organization) -> QuerySet[Account]:
    return accounts_for(organization, active_only=True).exclude(category__in=[AccountCategory.INVENTORY, AccountCategory.CASH_BANK])


def write_down_accounts(organization) -> QuerySet[Account]:
    return accounts_for(organization, active_only=True).filter(
        category__in=[AccountCategory.COST_OF_SALES, AccountCategory.OPERATING_EXPENSE, AccountCategory.OTHER_EXPENSE]
    )


@dataclass(frozen=True)
class StockRow:
    item: object
    quantity: Decimal
    value: Decimal

    @property
    def average_cost(self) -> Decimal:
        return (self.value / self.quantity).quantize(Decimal("0.01")) if self.quantity > 0 else ZERO


def stock_summary(organization) -> list[StockRow]:
    """Quantity and value on hand of every tracked item."""
    by_item = defaultdict(list)
    for movement in StockMovement.objects.for_organization(organization).order_by("id"):
        by_item[movement.item_id].append(movement)
    rows = []
    for item in tracked_items(organization).order_by("name"):
        stock = replay(by_item[item.pk])
        rows.append(StockRow(item, stock.quantity, stock.value))
    return rows


@dataclass(frozen=True)
class CardRow:
    movement: StockMovement
    quantity: Decimal  # on hand after the movement
    value: Decimal

    @property
    def unit_cost(self) -> Decimal:
        m = self.movement
        return (abs(m.value) / abs(m.quantity)).quantize(Decimal("0.01")) if m.quantity else ZERO


def stock_card(item) -> list[CardRow]:
    """The item's movements in posting order, with the running quantity and value (kartu stok)."""
    rows, quantity, value = [], Decimal("0"), ZERO
    movements = StockMovement.objects.filter(item=item).select_related("journal_entry", "invoice_line__invoice", "return_line__return_note").order_by("id")
    for m in movements:
        quantity += m.quantity
        value += m.value
        rows.append(CardRow(m, quantity, value))
    return rows


@dataclass(frozen=True)
class Reconciliation:
    account: Account
    stock: Decimal
    ledger: Decimal

    @property
    def difference(self) -> Decimal:
        return self.ledger - self.stock


def reconcile(organization, rows: list[StockRow], as_of: date) -> list[Reconciliation]:
    """Stock value per inventory account against the ledger. A difference means inventory
    posted outside the stock card (a journal entry, an untracked purchase) or the reverse."""
    totals, accounts = defaultdict(lambda: ZERO), {}
    for row in rows:
        accounts[row.item.inventory_account_id] = row.item.inventory_account
        totals[row.item.inventory_account_id] += row.value
    return [
        Reconciliation(account, totals[pk], account_balance(organization, account, as_of=as_of))
        for pk, account in sorted(accounts.items(), key=lambda item: item[1].code)
    ]


def stock_of(item) -> Stock:
    return replay(StockMovement.objects.filter(item=item).order_by("id"))
