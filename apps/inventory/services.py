"""Perpetual inventory: writes. Invoices call ``plan_*`` before posting their journal entry
(the cost lines go into it) and ``record`` after; adjustments post their own entry."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.db import transaction
from django.utils.translation import gettext as _

from apps.core.exceptions import ValidationFailed
from apps.core.money import quantize
from apps.core.validators import ensure_business_date
from apps.ledger.domain import AccountCategory
from apps.ledger.models import Account, JournalEntry
from apps.ledger.services import JournalLineInput, create_journal_entry

from .domain import QUANTITY_PLACES, InsufficientStock, MovementKind, Stock
from .models import StockMovement

RECEIPTS = (MovementKind.PURCHASE, MovementKind.SALE_RETURN, MovementKind.INCREASE)


def replay(movements: Iterable[StockMovement]) -> Stock:
    """An item's stock after its movements, in posting order."""
    stock = Stock()
    for m in movements:
        if m.kind in RECEIPTS:
            stock.receive(m.quantity, m.value, source=m.pk if m.kind == MovementKind.PURCHASE else None)
        elif m.kind == MovementKind.PURCHASE_REVERSAL:
            stock.remove(m.reverses_id, -m.quantity, -m.value)
        else:
            stock.issue(-m.quantity, m.cost_formula)
    return stock


def stock_of(item) -> Stock:
    return replay(StockMovement.objects.filter(item=item).order_by("id"))


def _lock(items) -> None:
    """Serialize postings per item: valuation depends on the order of movements."""
    from apps.invoicing.models import Item  # invoicing depends on inventory

    ids = sorted({item.pk for item in items})
    list(Item.objects.select_for_update().filter(pk__in=ids).order_by("pk").values_list("pk", flat=True))


@dataclass
class Planned:
    item: object
    kind: MovementKind
    quantity: Decimal  # signed
    value: Decimal  # signed
    cost_formula: str = ""
    invoice_line: object = None
    reverses: StockMovement | None = None
    return_line: object = None

    @property
    def cost(self) -> Decimal:
        return abs(self.value)


def _not_enough(item, available: Decimal) -> ValidationFailed:
    return ValidationFailed(
        _("Not enough %(item)s in stock: %(available)s %(unit)s available. Record the purchase or a stock increase first.")
        % {"item": item.name, "available": f"{available.normalize():f}", "unit": item.unit}
    )


def plan_purchase(lines_and_costs) -> list[Planned]:
    """``(invoice line, cost)`` pairs: tracked items enter stock at their cost."""
    planned = []
    for line, cost in lines_and_costs:
        if line.item is not None and line.item.track_inventory:
            planned.append(Planned(line.item, MovementKind.PURCHASE, line.quantity, quantize(cost), invoice_line=line))
    return planned


def plan_sale(organization, lines) -> list[Planned]:
    """Take sold quantities out of stock at their cost (the organization's cost formula)."""
    tracked = [line for line in lines if line.item is not None and line.item.track_inventory]
    _lock(line.item for line in tracked)
    stocks, planned = {}, []
    formula = organization.inventory_cost_formula
    for line in tracked:
        stock = stocks.setdefault(line.item.pk, stock_of(line.item))
        try:
            cost = stock.issue(line.quantity, formula)
        except InsufficientStock as exc:
            raise _not_enough(line.item, exc.available) from None
        planned.append(Planned(line.item, MovementKind.SALE, -line.quantity, -cost, formula, invoice_line=line))
    return planned


def _document_movements(invoice, kind: MovementKind) -> list[StockMovement]:
    return list(
        StockMovement.objects.filter(invoice_line__invoice=invoice, kind=kind, reversed_by__isnull=True)
        .select_related("item").order_by("id")
    )


def plan_sale_void(invoice) -> list[Planned]:
    """A voided sale's goods come back at the cost they went out at."""
    return [
        Planned(m.item, MovementKind.SALE_RETURN, -m.quantity, -m.value, invoice_line=m.invoice_line, reverses=m)
        for m in _document_movements(invoice, MovementKind.SALE)
    ]


def plan_purchase_void(invoice) -> list[Planned]:
    """A voided purchase takes its own stock back out, which must still be untouched."""
    movements = _document_movements(invoice, MovementKind.PURCHASE)
    _lock(m.item for m in movements)
    stocks, planned = {}, []
    for m in movements:
        stock = stocks.setdefault(m.item_id, stock_of(m.item))
        if not stock.can_remove(m.pk, m.quantity, m.value):
            raise ValidationFailed(
                _("Some of the %(item)s from this bill has already left stock, so the bill can't be voided. Record a stock decrease instead.")
                % {"item": m.item.name}
            )
        stock.remove(m.pk, m.quantity, m.value)
        planned.append(Planned(m.item, MovementKind.PURCHASE_REVERSAL, -m.quantity, -m.value, invoice_line=m.invoice_line, reverses=m))
    return planned


def plan_sales_return(lines_and_quantities) -> list[Planned]:
    """Returned goods come back at the cost they left at (pro rata; the last return of a line
    takes the rest, so the line's cost comes back exactly). Lines sold before the item's stock
    was tracked have no cost to bring back."""
    planned = []
    for line, quantity in lines_and_quantities:
        sale = StockMovement.objects.filter(invoice_line=line, kind=MovementKind.SALE).select_related("item").first()
        if sale is None:
            continue
        earlier = StockMovement.objects.filter(invoice_line=line, kind=MovementKind.SALE_RETURN, return_line__isnull=False)
        returned = sum((m.quantity for m in earlier), Decimal("0"))
        returned_value = sum((m.value for m in earlier), Decimal("0"))
        sold, cost = -sale.quantity, -sale.value
        value = cost - returned_value if returned + quantity == sold else quantize(cost * quantity / sold)
        planned.append(Planned(sale.item, MovementKind.SALE_RETURN, quantity, value, invoice_line=line))
    return planned


def plan_purchase_return(organization, lines_and_quantities) -> list[Planned]:
    """Goods sent back to the supplier leave stock at their cost under the organization's
    formula (they may have been valued together with later purchases)."""
    received = {
        m.invoice_line_id: m
        for m in StockMovement.objects.filter(
            invoice_line__in=[line for line, _q in lines_and_quantities], kind=MovementKind.PURCHASE
        ).select_related("item")
    }
    pairs = [(line, quantity, received[line.pk]) for line, quantity in lines_and_quantities if line.pk in received]
    _lock(m.item for _l, _q, m in pairs)
    stocks, planned = {}, []
    formula = organization.inventory_cost_formula
    for line, quantity, receipt in pairs:
        stock = stocks.setdefault(receipt.item_id, stock_of(receipt.item))
        try:
            cost = stock.issue(quantity, formula)
        except InsufficientStock as exc:
            raise _not_enough(receipt.item, exc.available) from None
        planned.append(Planned(receipt.item, MovementKind.PURCHASE_RETURN, -quantity, -cost, formula, invoice_line=line))
    return planned


def cost_journal_lines(planned: list[Planned]) -> list[JournalLineInput]:
    """Cost of goods sold for sales (Dr cost of goods sold, Cr inventory); mirrored for returns."""
    lines = []
    for p in planned:
        if not p.cost:
            continue
        label = p.item.name
        if p.kind == MovementKind.SALE:
            lines.append(JournalLineInput(p.item.cogs_account, debit=p.cost, description=label))
            lines.append(JournalLineInput(p.item.inventory_account, credit=p.cost, description=label))
        elif p.kind == MovementKind.SALE_RETURN and p.reverses is None:  # a sales return document
            lines.append(JournalLineInput(p.item.inventory_account, debit=p.cost, description=label))
            lines.append(JournalLineInput(p.item.cogs_account, credit=p.cost, description=label))
    return lines


def record(planned: list[Planned], *, entry: JournalEntry, on: date, created_by=None, reason: str = "") -> list[StockMovement]:
    return StockMovement.objects.bulk_create(
        StockMovement(
            organization=entry.organization, item=p.item, date=on, kind=p.kind, quantity=p.quantity, value=p.value,
            cost_formula=p.cost_formula, invoice_line=p.invoice_line, reverses=p.reverses, return_line=p.return_line,
            journal_entry=entry,
            reason=reason, created_by=created_by,
        )
        for p in planned
    )


@transaction.atomic
def adjust_stock(
    *, organization, item, on: date, quantity: Decimal, counter_account: Account, reason: str,
    unit_cost: Decimal | None = None, created_by=None,
) -> StockMovement:
    """Opening stock or a count difference. An increase enters at ``unit_cost`` (default: the
    current average cost); a decrease leaves at its cost under the organization's formula.
    The other side goes to ``counter_account``: e.g. Modal Pemilik for opening stock, cost of
    goods sold or an other expense for shrinkage."""
    if item.organization_id != organization.pk or not item.track_inventory:
        raise ValidationFailed(_("Choose an item whose stock is tracked."))
    if counter_account.organization_id != organization.pk or not counter_account.is_active or counter_account.category == AccountCategory.INVENTORY:
        raise ValidationFailed(_("Choose the account for the other side of the adjustment (not an inventory account)."))
    if not reason.strip():
        raise ValidationFailed(_("Give the reason for the adjustment."))
    ensure_business_date(on)
    quantity = Decimal(quantity).quantize(QUANTITY_PLACES)
    if quantity == 0:
        raise ValidationFailed(_("Enter the quantity to add (positive) or remove (negative)."))
    _lock([item])
    stock = stock_of(item)
    if quantity > 0:
        unit = stock.average_cost if unit_cost is None else Decimal(unit_cost)
        if unit < 0:
            raise ValidationFailed(_("The unit cost can't be negative."))
        planned = Planned(item, MovementKind.INCREASE, quantity, quantize(unit * quantity))
        lines = [JournalLineInput(item.inventory_account, debit=planned.cost), JournalLineInput(counter_account, credit=planned.cost)]
    else:
        formula = organization.inventory_cost_formula
        try:
            cost = stock.issue(-quantity, formula)
        except InsufficientStock as exc:
            raise _not_enough(item, exc.available) from None
        planned = Planned(item, MovementKind.DECREASE, quantity, -cost, formula)
        lines = [JournalLineInput(counter_account, debit=cost), JournalLineInput(item.inventory_account, credit=cost)]
    if planned.cost == 0:
        raise ValidationFailed(_("The adjustment has no value, so there is nothing to post. Enter a unit cost."))
    # Journal descriptions are stored business records and stay in Indonesian.
    entry = create_journal_entry(
        organization=organization, date=on, description=f"Penyesuaian persediaan {item.name}: {reason.strip()}"[:255],
        source=JournalEntry.Source.INVENTORY, created_by=created_by, lines=lines,
    )
    return record([planned], entry=entry, on=on, created_by=created_by, reason=reason.strip())[0]
