from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import F, Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import TenantOwnedModel, TimeStampedModel, money_field
from apps.contacts.validators import format_npwp
from apps.core.validators import code_validator, tax_invoice_number_validator

from .domain import InvoiceStatus, LineSpec, TaxSpec


class InvoiceKind(models.TextChoices):
    SALES = "sales", _("Sales")
    PURCHASE = "purchase", _("Purchase")

    @property
    def payment_label(self) -> str:
        """How payments against this kind of invoice are called."""
        return _("Receipts (sales)") if self == InvoiceKind.SALES else _("Disbursements (purchases)")


class Item(TenantOwnedModel):
    """Product or service catalog entry used to pre-fill invoice lines."""

    class Type(models.TextChoices):
        GOODS = "goods", _("Goods")
        SERVICE = "service", _("Service")

    code = models.CharField(_("code/SKU"), max_length=50, blank=True, validators=[code_validator])
    name = models.CharField(_("name"), max_length=200)
    type = models.CharField(_("type"), max_length=10, choices=Type.choices, default=Type.GOODS)
    unit = models.CharField(_("unit"), max_length=20, default="pcs")
    sale_price = money_field(_("sale price"), validators=[MinValueValidator(0)])
    purchase_price = money_field(_("purchase price"), validators=[MinValueValidator(0)])
    income_account = models.ForeignKey(
        "ledger.Account", null=True, blank=True, on_delete=models.PROTECT, related_name="+", verbose_name=_("revenue account")
    )
    expense_account = models.ForeignKey(
        "ledger.Account", null=True, blank=True, on_delete=models.PROTECT, related_name="+", verbose_name=_("purchase/expense account")
    )
    tax_rate = models.ForeignKey(
        "taxes.TaxRate", null=True, blank=True, on_delete=models.SET_NULL, related_name="+", verbose_name=_("default tax")
    )
    # Perpetual inventory (apps.inventory): purchases go to the inventory account and each sale
    # posts its cost (FIFO or weighted average) to cost of goods sold. Fixed once the item has
    # stock movements (inventory migration 0002).
    track_inventory = models.BooleanField(
        _("track stock"), default=False,
        help_text=_("Keep stock quantities and value: purchases add to inventory, and each sale posts its cost of goods sold."),
    )
    inventory_account = models.ForeignKey(
        "ledger.Account", null=True, blank=True, on_delete=models.PROTECT, related_name="+", verbose_name=_("inventory account")
    )
    cogs_account = models.ForeignKey(
        "ledger.Account", null=True, blank=True, on_delete=models.PROTECT, related_name="+", verbose_name=_("cost of goods sold account")
    )
    is_active = models.BooleanField(_("active"), default=True)

    FIXED_ONCE_STOCKED = ("type", "track_inventory", "inventory_account", "cogs_account")

    class Meta:
        ordering = ["name"]
        constraints = [
            models.CheckConstraint(
                name="tracked_items_are_goods_with_accounts",
                condition=models.Q(track_inventory=False) | models.Q(
                    type="goods", inventory_account__isnull=False, cogs_account__isnull=False,
                ),
            ),
        ]
        verbose_name = _("item")
        verbose_name_plural = _("items")

    def __str__(self):
        return f"{self.code} · {self.name}" if self.code else self.name


class Invoice(TenantOwnedModel):
    Status = InvoiceStatus

    OPEN_STATUSES = (Status.ISSUED, Status.PARTIAL)

    kind = models.CharField(_("type"), max_length=10, choices=InvoiceKind.choices)
    number = models.CharField(_("number"), max_length=30, blank=True)
    contact = models.ForeignKey("contacts.Contact", on_delete=models.PROTECT, related_name="invoices", verbose_name=_("contact"))
    date = models.DateField(_("date"))
    due_date = models.DateField(_("due date"))
    reference = models.CharField(_("reference no."), max_length=100, blank=True, help_text=_("PO number or supplier invoice number."))
    tax_invoice_number = models.CharField(
        _("tax invoice no."), max_length=30, blank=True, validators=[tax_invoice_number_validator], help_text=_("Faktur Pajak serial number (e-Faktur/Coretax).")
    )
    notes = models.TextField(_("notes"), max_length=2000, blank=True)
    status = models.CharField(_("status"), max_length=10, choices=Status.choices, default=Status.DRAFT)

    subtotal = money_field(_("subtotal"))
    tax_amount = money_field(_("PPN (VAT)"))
    total = money_field(_("total"))
    amount_paid = money_field(_("amount paid"))
    # Returns credited against the balance (not the part refunded in cash): with the payments,
    # it settles the invoice. The database checks it against the posted returns (invoicing 0011).
    amount_credited = money_field(_("returns credited"))

    journal_entry = models.OneToOneField(
        "ledger.JournalEntry", null=True, blank=True, on_delete=models.PROTECT, related_name="invoice"
    )
    issued_at = models.DateTimeField(null=True, blank=True)
    # Parties as they were when the invoice was issued (frozen with it; contacts and the
    # organization stay editable for future invoices). Empty while a draft.
    contact_name = models.CharField(_("contact name"), max_length=200, blank=True, editable=False)
    contact_npwp = models.CharField(_("contact NPWP"), max_length=16, blank=True, editable=False)
    contact_address = models.TextField(_("contact address"), blank=True, editable=False)
    contact_city = models.CharField(_("contact city"), max_length=100, blank=True, editable=False)
    seller_name = models.CharField(_("seller name"), max_length=200, blank=True, editable=False)
    seller_npwp = models.CharField(_("seller NPWP"), max_length=16, blank=True, editable=False)
    seller_address = models.TextField(_("seller address"), blank=True, editable=False)
    seller_city = models.CharField(_("seller city"), max_length=100, blank=True, editable=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-date", "-id"]
        verbose_name = _("invoice")
        verbose_name_plural = _("invoices")
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "number"], condition=~Q(number=""), name="unique_invoice_number"
            ),
        ]
        indexes = [models.Index(fields=["organization", "kind", "status"])]

    def __str__(self):
        return self.display_number

    @property
    def display_number(self) -> str:
        return self.number or _("DRAFT-%(id)s") % {"id": self.pk}

    def snapshot_parties(self) -> None:
        """Copy the buyer's and the organization's details onto the invoice (at issue)."""
        contact, organization = self.contact, self.organization
        self.contact_name, self.contact_npwp = contact.name, contact.npwp
        self.contact_address, self.contact_city = contact.address, contact.city
        self.seller_name, self.seller_npwp = organization.name, organization.npwp
        self.seller_address, self.seller_city = organization.address, organization.city

    def _party(self, snapshot: str, live) -> str:
        """Issued invoices show the parties as they were; drafts show the current details."""
        return getattr(self, snapshot) if self.status != self.Status.DRAFT else live

    @property
    def party_name(self) -> str:
        return self._party("contact_name", self.contact.name)

    @property
    def party_npwp(self) -> str:
        return format_npwp(self._party("contact_npwp", self.contact.npwp))

    @property
    def party_address(self) -> str:
        return self._party("contact_address", self.contact.address)

    @property
    def party_city(self) -> str:
        return self._party("contact_city", self.contact.city)

    @property
    def seller(self) -> dict:
        org = self.organization
        if self.status == self.Status.DRAFT:
            return {"name": org.name, "npwp": org.npwp, "address": org.address, "city": org.city}
        return {"name": self.seller_name, "npwp": self.seller_npwp, "address": self.seller_address, "city": self.seller_city}

    @property
    def balance_due(self):
        return self.total - self.amount_paid - self.amount_credited

    @property
    def status_label(self) -> str:
        """The status as shown: an invoice settled by returns alone wasn't paid."""
        if self.amount_credited and not self.amount_paid:
            if self.status == self.Status.PAID:
                return _("Returned")
            if self.status == self.Status.PARTIAL:
                return _("Partly returned")
        return self.get_status_display()

    @property
    def can_return(self) -> bool:
        return self.status in (self.Status.ISSUED, self.Status.PARTIAL, self.Status.PAID)

    @property
    def is_sales(self) -> bool:
        return self.kind == InvoiceKind.SALES

    @property
    def is_open(self) -> bool:
        return self.status in self.OPEN_STATUSES

    @property
    def is_overdue(self) -> bool:
        return self.is_open and self.due_date < timezone.localdate()

    @property
    def days_overdue(self) -> int:
        return (timezone.localdate() - self.due_date).days


class InvoiceLine(TimeStampedModel):
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="lines")
    item = models.ForeignKey(Item, null=True, blank=True, on_delete=models.SET_NULL, related_name="+", verbose_name=_("item"))
    description = models.CharField(_("item description"), max_length=255)
    account = models.ForeignKey("ledger.Account", on_delete=models.PROTECT, related_name="+", verbose_name=_("account"))
    quantity = models.DecimalField(_("quantity"), max_digits=14, decimal_places=3, default=1)
    unit_price = money_field(_("unit price"))
    discount_percent = models.DecimalField(_("discount (%)"), max_digits=5, decimal_places=2, default=0)
    tax_rate = models.ForeignKey("taxes.TaxRate", null=True, blank=True, on_delete=models.PROTECT, related_name="+", verbose_name=_("tax"))
    amount = money_field(_("amount"))
    # The account as named when the invoice was issued, written by the database (invoicing
    # migration 0006) and frozen with the line. Empty on drafts.
    account_code = models.CharField(_("account code when issued"), max_length=20, blank=True, editable=False)
    account_name = models.CharField(_("account name when issued"), max_length=150, blank=True, editable=False)
    # The tax rate as it was at issue (same migration 0007 pattern); empty without tax.
    tax_name = models.CharField(_("tax name when issued"), max_length=100, blank=True, editable=False)
    tax_rate_percent = models.DecimalField(_("tax rate when issued (%)"), max_digits=5, decimal_places=2, null=True, blank=True, editable=False)
    tax_dpp_numerator = models.PositiveSmallIntegerField(_("DPP numerator when issued"), null=True, blank=True, editable=False)
    tax_dpp_denominator = models.PositiveSmallIntegerField(_("DPP denominator when issued"), null=True, blank=True, editable=False)
    item_code = models.CharField(_("item code when issued"), max_length=50, blank=True, editable=False)  # migration 0008

    class Meta:
        ordering = ["id"]

    @property
    def is_snapshot(self) -> bool:
        """Issued lines carry their account and tax as they were at issue; drafts follow the current data."""
        return bool(self.account_code)

    @property
    def account_label(self) -> str:
        return f"{self.account_code} · {self.account_name}" if self.is_snapshot else str(self.account)

    @property
    def item_label(self) -> str:
        if self.is_snapshot:
            return self.item_code
        return self.item.code if self.item_id else ""

    @property
    def tax_label(self) -> str:
        if self.is_snapshot:
            return self.tax_name
        return str(self.tax_rate) if self.tax_rate_id else ""

    def to_spec(self) -> LineSpec:
        tax = None
        if self.tax_rate_id:
            tax = TaxSpec(self.tax_rate_id, self.tax_rate.rate, self.tax_rate.dpp_numerator, self.tax_rate.dpp_denominator)
        return LineSpec(self.quantity, self.unit_price, self.discount_percent, tax)


# Invoice list filter: every stored status plus the derived "overdue" (open and past due).
InvoiceStatusFilter = models.TextChoices(
    "InvoiceStatusFilter",
    [(status.name, (status.value, status.label)) for status in Invoice.Status] + [("OVERDUE", ("overdue", _("Overdue")))],
)


class Payment(TenantOwnedModel):
    class Method(models.TextChoices):
        CASH = "cash", _("Cash")
        TRANSFER = "transfer", _("Bank transfer")
        QRIS = "qris", _("QRIS")
        EWALLET = "ewallet", _("E-wallet")
        GIRO = "giro", _("Cheque/Giro")

    class Status(models.TextChoices):
        POSTED = "posted", _("Recorded")
        VOID = "void", _("Void")

    number = models.CharField(_("number"), max_length=30)
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="payments", verbose_name=_("invoice"))
    date = models.DateField(_("date"))
    amount = money_field(_("amount"), validators=[MinValueValidator(Decimal("0.01"))])
    cash_account = models.ForeignKey("ledger.Account", on_delete=models.PROTECT, related_name="+", verbose_name=_("cash/bank account"))
    method = models.CharField(_("method"), max_length=10, choices=Method.choices, default=Method.TRANSFER)
    reference = models.CharField(_("reference"), max_length=100, blank=True)
    status = models.CharField(_("status"), max_length=10, choices=Status.choices, default=Status.POSTED)
    journal_entry = models.OneToOneField("ledger.JournalEntry", on_delete=models.PROTECT, related_name="payment")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-date", "-id"]
        verbose_name = _("payment")
        verbose_name_plural = _("payments")
        constraints = [
            models.UniqueConstraint(fields=["organization", "number"], name="unique_payment_number"),
            models.CheckConstraint(condition=Q(amount__gt=0), name="payment_amount_positive"),
        ]

    def __str__(self):
        return self.number

    @property
    def is_receipt(self) -> bool:
        return self.invoice.kind == InvoiceKind.SALES


class ReturnNote(TenantOwnedModel):
    """A sales return (retur penjualan) or purchase return (retur pembelian) of an issued
    invoice: the nota retur. Its lines take back quantities of the invoice's lines at their
    issued price, discount and tax rate. The total first reduces what is still owed on the
    invoice (``credited``); the rest is refunded in cash (``refunded``).

    Final once posted: inserted with its posted journal entry and never changed or deleted
    (invoicing migration 0011); a mistake is undone by voiding it, which reverses its journal
    entry and its stock (invoicing 0012).
    """

    class Status(models.TextChoices):
        POSTED = "posted", _("Recorded")
        VOID = "void", _("Void")

    kind = models.CharField(_("type"), max_length=10, choices=InvoiceKind.choices, editable=False)
    status = models.CharField(_("status"), max_length=10, choices=Status.choices, default=Status.POSTED)
    number = models.CharField(_("number"), max_length=30)
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="returns", verbose_name=_("invoice"))
    date = models.DateField(_("date"))
    reason = models.CharField(_("reason"), max_length=255)
    subtotal = money_field(_("subtotal"))
    tax_amount = money_field(_("PPN (VAT)"))
    total = money_field(_("total"))
    credited = money_field(_("credited to the invoice"))
    refunded = money_field(_("refunded"))
    cash_account = models.ForeignKey(
        "ledger.Account", null=True, blank=True, on_delete=models.PROTECT, related_name="+", verbose_name=_("refund cash/bank account")
    )
    journal_entry = models.OneToOneField("ledger.JournalEntry", on_delete=models.PROTECT, related_name="return_note")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-date", "-id"]
        verbose_name = _("return")
        verbose_name_plural = _("returns")
        constraints = [
            models.UniqueConstraint(fields=["organization", "number"], name="unique_return_number"),
            models.CheckConstraint(
                name="return_is_credited_or_refunded",
                condition=Q(total__gt=0, credited__gte=0, refunded__gte=0, total=F("credited") + F("refunded")),
            ),
        ]

    def __str__(self):
        return self.number

    @property
    def is_sales(self) -> bool:
        return self.kind == InvoiceKind.SALES


class ReturnLine(TimeStampedModel):
    return_note = models.ForeignKey(ReturnNote, on_delete=models.CASCADE, related_name="lines")
    invoice_line = models.ForeignKey(InvoiceLine, on_delete=models.PROTECT, related_name="returns", verbose_name=_("invoice line"))
    quantity = models.DecimalField(_("quantity"), max_digits=14, decimal_places=3, validators=[MinValueValidator(Decimal("0.001"))])
    amount = money_field(_("amount"))

    class Meta:
        ordering = ["id"]
        verbose_name = _("return line")
        verbose_name_plural = _("return lines")

    def __str__(self):
        return f"{self.return_note.number}: {self.quantity} x {self.invoice_line.description}"
