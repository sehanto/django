from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TenantOwnedModel, money_field

from .domain import CostFormula, MovementKind


class StockMovement(TenantOwnedModel):
    """One change of an item's stock, posted with its journal entry: the stock card.

    Append-only in the database (inventory migration 0002). The order of posting (``id``)
    is the order of valuation. Quantities and values are signed: receipts positive,
    issues negative.
    """

    Kind = MovementKind

    item = models.ForeignKey("invoicing.Item", on_delete=models.PROTECT, related_name="stock_movements", verbose_name=_("item"))
    date = models.DateField(_("date"))
    kind = models.CharField(_("type"), max_length=30, choices=MovementKind.choices)
    quantity = models.DecimalField(_("quantity"), max_digits=14, decimal_places=3)
    value = money_field(_("value"))
    cost_formula = models.CharField(_("cost formula"), max_length=20, choices=CostFormula.choices, blank=True, help_text=_("For issues: how their cost was taken."))
    invoice_line = models.ForeignKey("invoicing.InvoiceLine", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    return_line = models.ForeignKey("invoicing.ReturnLine", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    reverses = models.OneToOneField("self", null=True, blank=True, on_delete=models.PROTECT, related_name="reversed_by")
    journal_entry = models.ForeignKey("ledger.JournalEntry", on_delete=models.PROTECT, related_name="+")
    reason = models.CharField(_("reason"), max_length=255, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["id"]
        verbose_name = _("stock movement")
        verbose_name_plural = _("stock movements")
        constraints = [
            models.CheckConstraint(
                name="stock_movement_sign_follows_kind",
                condition=(
                    models.Q(
                        kind__in=[MovementKind.PURCHASE, MovementKind.SALE_RETURN, MovementKind.INCREASE, MovementKind.PURCHASE_RETURN_VOIDED],
                        quantity__gt=0, value__gte=0,
                    )
                    | models.Q(
                        kind__in=[MovementKind.SALE, MovementKind.PURCHASE_REVERSAL, MovementKind.DECREASE, MovementKind.PURCHASE_RETURN,
                                  MovementKind.SALE_RETURN_VOIDED],
                        quantity__lt=0, value__lte=0,
                    )
                    | models.Q(kind=MovementKind.WRITE_DOWN, quantity=0, value__lt=0)
                ),
            ),
        ]

    def __str__(self):
        return f"{self.item} {self.quantity} ({self.get_kind_display()})"
