"""Perpetual inventory valuation (pure functions, no database access).

SAK EMKM measures inventory at cost, with FIFO or weighted average as the cost formula.
An item's stock is a list of cost layers, built by replaying its movements in the order
they were posted:

* a receipt (purchase, sales return, increase) adds a layer at its cost;
* an issue (sale, decrease) takes its cost from the layers: FIFO from the oldest layers
  first; weighted average at the average cost of everything in stock, after which the
  stock is one layer at that average (a moving average);
* a voided purchase or sales return removes its own layer, which must still be whole;
  a voided purchase return brings its goods back as a new layer at the cost they left at.

Costs are kept to the cent and every issue reduces the stock value by exactly its cost,
so the stock value always equals the inventory postings.
"""

from dataclasses import dataclass, field
from decimal import Decimal

from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.money import ZERO, quantize

QUANTITY_PLACES = Decimal("0.001")


class CostFormula(models.TextChoices):
    """The cost formulas SAK EMKM allows for inventory."""

    FIFO = "fifo", _("First in, first out (FIFO)")
    WEIGHTED_AVERAGE = "weighted_average", _("Weighted average")


class MovementKind(models.TextChoices):
    PURCHASE = "purchase", _("Purchase")
    SALE = "sale", _("Sale")
    SALE_RETURN = "sale_return", _("Sale voided or returned")
    PURCHASE_REVERSAL = "purchase_reversal", _("Purchase voided")
    PURCHASE_RETURN = "purchase_return", _("Returned to supplier")
    SALE_RETURN_VOIDED = "sale_return_voided", _("Sales return voided")
    PURCHASE_RETURN_VOIDED = "purchase_return_voided", _("Purchase return voided")
    INCREASE = "increase", _("Stock increase")
    DECREASE = "decrease", _("Stock decrease")

    @property
    def is_issue(self) -> bool:
        return self in (MovementKind.SALE, MovementKind.DECREASE, MovementKind.PURCHASE_RETURN)

    @property
    def removes_a_layer(self) -> bool:
        """Takes a whole receipt back out (a voided purchase or sales return)."""
        return self in (MovementKind.PURCHASE_REVERSAL, MovementKind.SALE_RETURN_VOIDED)


class InsufficientStock(Exception):
    def __init__(self, available: Decimal):
        super().__init__(f"only {available} in stock")
        self.available = available


@dataclass
class Layer:
    quantity: Decimal
    value: Decimal
    source: int | None = None  # the receipt that created it, while it is whole


@dataclass
class Stock:
    layers: list[Layer] = field(default_factory=list)

    @property
    def quantity(self) -> Decimal:
        return sum((layer.quantity for layer in self.layers), Decimal("0"))

    @property
    def value(self) -> Decimal:
        return sum((layer.value for layer in self.layers), ZERO)

    @property
    def average_cost(self) -> Decimal:
        quantity = self.quantity
        return quantize(self.value / quantity) if quantity > 0 else ZERO

    def receive(self, quantity: Decimal, value: Decimal, source: int | None = None) -> None:
        if quantity <= 0:
            raise ValueError("a receipt adds a positive quantity")
        self.layers.append(Layer(quantity, quantize(value), source))

    def issue(self, quantity: Decimal, formula: str) -> Decimal:
        """Take ``quantity`` out of stock; return its cost."""
        if quantity <= 0:
            raise ValueError("an issue takes a positive quantity")
        available = self.quantity
        if quantity > available:
            raise InsufficientStock(available)
        if formula == CostFormula.WEIGHTED_AVERAGE:
            total = self.value
            cost = total if quantity == available else quantize(total * quantity / available)
            remaining = available - quantity
            self.layers = [Layer(remaining, total - cost)] if remaining > 0 else []
            return cost
        cost, needed = ZERO, quantity
        while needed > 0:
            layer = self.layers[0]
            if layer.quantity <= needed:
                cost += layer.value
                needed -= layer.quantity
                self.layers.pop(0)
            else:
                part = quantize(layer.value * needed / layer.quantity)
                cost += part
                self.layers[0] = Layer(layer.quantity - needed, layer.value - part)  # no longer whole
                needed = Decimal("0")
        return cost

    def can_remove(self, source: int, quantity: Decimal, value: Decimal) -> bool:
        return any(l.source == source and l.quantity == quantity and l.value == quantize(value) for l in self.layers)

    def remove(self, source: int, quantity: Decimal, value: Decimal) -> None:
        """Take a reversed receipt's whole layer back out."""
        for index, layer in enumerate(self.layers):
            if layer.source == source and layer.quantity == quantity and layer.value == quantize(value):
                del self.layers[index]
                return
        raise ValueError("the receipt's stock is no longer whole")
