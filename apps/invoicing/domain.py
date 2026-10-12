"""Invoice arithmetic and lifecycle rules (pure functions)."""

from collections import OrderedDict
from collections.abc import Hashable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from django.utils.translation import gettext_lazy as _

from apps.core.exceptions import ValidationFailed
from apps.core.money import ZERO, quantize
from apps.taxes.domain import compute_vat

HUNDRED = Decimal("100")


@dataclass(frozen=True)
class TaxSpec:
    key: Hashable
    rate: Decimal
    dpp_numerator: int = 1
    dpp_denominator: int = 1


@dataclass(frozen=True)
class LineSpec:
    quantity: Decimal
    unit_price: Decimal
    discount_percent: Decimal = ZERO
    tax: TaxSpec | None = None

    @property
    def net_amount(self) -> Decimal:
        if self.quantity <= 0:
            raise ValidationFailed(_("Quantity must be greater than zero."))
        if self.unit_price < 0:
            raise ValidationFailed(_("Unit price cannot be negative."))
        if not ZERO <= self.discount_percent <= HUNDRED:
            raise ValidationFailed(_("Discount must be between 0 and 100%."))
        gross = self.quantity * self.unit_price
        return quantize(gross - gross * self.discount_percent / HUNDRED)


@dataclass
class TaxGroup:
    spec: TaxSpec
    base: Decimal = ZERO
    dpp: Decimal = ZERO
    tax: Decimal = ZERO
    line_indexes: list[int] = field(default_factory=list)


@dataclass
class InvoiceTotals:
    line_amounts: list[Decimal]
    tax_groups: list[TaxGroup]
    subtotal: Decimal
    tax_total: Decimal

    @property
    def total(self) -> Decimal:
        return self.subtotal + self.tax_total


def compute_invoice_totals(lines: Sequence[LineSpec]) -> InvoiceTotals:
    """Tax is computed per rate on the aggregated base, like an e-Faktur."""
    if not lines:
        raise ValidationFailed(_("An invoice needs at least one line."))
    amounts = [line.net_amount for line in lines]
    groups: OrderedDict[Hashable, TaxGroup] = OrderedDict()
    for index, (line, amount) in enumerate(zip(lines, amounts)):
        if line.tax is None:
            continue
        group = groups.setdefault(line.tax.key, TaxGroup(spec=line.tax))
        group.base += amount
        group.line_indexes.append(index)
    for group in groups.values():
        vat = compute_vat(group.base, group.spec.rate, group.spec.dpp_numerator, group.spec.dpp_denominator)
        group.dpp, group.tax = vat.dpp, vat.tax
    subtotal = sum(amounts, ZERO)
    tax_total = sum((g.tax for g in groups.values()), ZERO)
    return InvoiceTotals(amounts, list(groups.values()), subtotal, tax_total)


def allocate(amount: Decimal, weights: Sequence[Decimal]) -> list[Decimal]:
    """Split ``amount`` proportionally to ``weights``; rounding goes to the last share."""
    total_weight = sum(weights, ZERO)
    if not weights:
        return []
    if total_weight == 0:
        return [ZERO] * (len(weights) - 1) + [quantize(amount)]
    shares = [quantize(amount * w / total_weight) for w in weights[:-1]]
    shares.append(quantize(amount) - sum(shares, ZERO))
    return shares


class PaymentState:
    ISSUED = "issued"
    PARTIAL = "partial"
    PAID = "paid"


def payment_state(total: Decimal, amount_paid: Decimal) -> str:
    if amount_paid <= 0:
        return PaymentState.ISSUED
    if amount_paid >= total:
        return PaymentState.PAID
    return PaymentState.PARTIAL


def aging_bucket(days_overdue: int) -> str:
    if days_overdue <= 0:
        return "current"
    if days_overdue <= 30:
        return "d1_30"
    if days_overdue <= 60:
        return "d31_60"
    if days_overdue <= 90:
        return "d61_90"
    return "d90_plus"


AGING_BUCKETS = [
    ("current", _("Not yet due")),
    ("d1_30", _("1-30 days")),
    ("d31_60", _("31-60 days")),
    ("d61_90", _("61-90 days")),
    ("d90_plus", _("> 90 days")),
]
