from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.core.models import TenantOwnedModel, TimeStampedModel, money_field

from .domain import LineSpec, TaxSpec


class InvoiceKind(models.TextChoices):
    SALES = "sales", "Penjualan"
    PURCHASE = "purchase", "Pembelian"


class Item(TenantOwnedModel):
    """Product or service catalog entry used to pre-fill invoice lines."""

    class Type(models.TextChoices):
        GOODS = "goods", "Barang"
        SERVICE = "service", "Jasa"

    code = models.CharField("kode/SKU", max_length=50, blank=True)
    name = models.CharField("nama", max_length=200)
    type = models.CharField("jenis", max_length=10, choices=Type.choices, default=Type.GOODS)
    unit = models.CharField("satuan", max_length=20, default="pcs")
    sale_price = money_field("harga jual")
    purchase_price = money_field("harga beli")
    income_account = models.ForeignKey(
        "ledger.Account", null=True, blank=True, on_delete=models.PROTECT, related_name="+", verbose_name="akun pendapatan"
    )
    expense_account = models.ForeignKey(
        "ledger.Account", null=True, blank=True, on_delete=models.PROTECT, related_name="+", verbose_name="akun pembelian/beban"
    )
    tax_rate = models.ForeignKey(
        "taxes.TaxRate", null=True, blank=True, on_delete=models.SET_NULL, related_name="+", verbose_name="pajak default"
    )
    is_active = models.BooleanField("aktif", default=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "barang/jasa"
        verbose_name_plural = "barang/jasa"

    def __str__(self):
        return f"{self.code} · {self.name}" if self.code else self.name


class Invoice(TenantOwnedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draf"
        ISSUED = "issued", "Belum Dibayar"
        PARTIAL = "partial", "Dibayar Sebagian"
        PAID = "paid", "Lunas"
        VOID = "void", "Dibatalkan"

    OPEN_STATUSES = (Status.ISSUED, Status.PARTIAL)

    kind = models.CharField("jenis", max_length=10, choices=InvoiceKind.choices)
    number = models.CharField("nomor", max_length=30, blank=True)
    contact = models.ForeignKey("contacts.Contact", on_delete=models.PROTECT, related_name="invoices", verbose_name="kontak")
    date = models.DateField("tanggal")
    due_date = models.DateField("jatuh tempo")
    reference = models.CharField("no. referensi", max_length=100, blank=True, help_text="No. PO / no. faktur pemasok.")
    tax_invoice_number = models.CharField(
        "no. faktur pajak", max_length=30, blank=True, help_text="Nomor seri Faktur Pajak (e-Faktur/Coretax)."
    )
    notes = models.TextField("catatan", blank=True)
    status = models.CharField("status", max_length=10, choices=Status.choices, default=Status.DRAFT)

    subtotal = money_field("subtotal")
    tax_amount = money_field("PPN")
    total = money_field("total")
    amount_paid = money_field("telah dibayar")

    journal_entry = models.OneToOneField(
        "ledger.JournalEntry", null=True, blank=True, on_delete=models.PROTECT, related_name="invoice"
    )
    issued_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-date", "-id"]
        verbose_name = "faktur"
        verbose_name_plural = "faktur"
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
        return self.number or f"DRAF-{self.pk}"

    @property
    def balance_due(self):
        return self.total - self.amount_paid

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
    item = models.ForeignKey(Item, null=True, blank=True, on_delete=models.SET_NULL, related_name="+", verbose_name="barang/jasa")
    description = models.CharField("deskripsi", max_length=255)
    account = models.ForeignKey("ledger.Account", on_delete=models.PROTECT, related_name="+", verbose_name="akun")
    quantity = models.DecimalField("kuantitas", max_digits=14, decimal_places=3, default=1)
    unit_price = money_field("harga satuan")
    discount_percent = models.DecimalField("diskon (%)", max_digits=5, decimal_places=2, default=0)
    tax_rate = models.ForeignKey("taxes.TaxRate", null=True, blank=True, on_delete=models.PROTECT, related_name="+", verbose_name="pajak")
    amount = money_field("jumlah")

    class Meta:
        ordering = ["id"]

    def to_spec(self) -> LineSpec:
        tax = None
        if self.tax_rate_id:
            tax = TaxSpec(self.tax_rate_id, self.tax_rate.rate, self.tax_rate.dpp_numerator, self.tax_rate.dpp_denominator)
        return LineSpec(self.quantity, self.unit_price, self.discount_percent, tax)


class Payment(TenantOwnedModel):
    class Method(models.TextChoices):
        CASH = "cash", "Tunai"
        TRANSFER = "transfer", "Transfer Bank"
        QRIS = "qris", "QRIS"
        EWALLET = "ewallet", "E-Wallet"
        GIRO = "giro", "Cek/Giro"

    class Status(models.TextChoices):
        POSTED = "posted", "Tercatat"
        VOID = "void", "Dibatalkan"

    number = models.CharField("nomor", max_length=30)
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="payments", verbose_name="faktur")
    date = models.DateField("tanggal")
    amount = money_field("jumlah")
    cash_account = models.ForeignKey("ledger.Account", on_delete=models.PROTECT, related_name="+", verbose_name="akun kas/bank")
    method = models.CharField("metode", max_length=10, choices=Method.choices, default=Method.TRANSFER)
    reference = models.CharField("referensi", max_length=100, blank=True)
    status = models.CharField("status", max_length=10, choices=Status.choices, default=Status.POSTED)
    journal_entry = models.OneToOneField("ledger.JournalEntry", on_delete=models.PROTECT, related_name="payment")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-date", "-id"]
        verbose_name = "pembayaran"
        verbose_name_plural = "pembayaran"
        constraints = [
            models.UniqueConstraint(fields=["organization", "number"], name="unique_payment_number"),
            models.CheckConstraint(condition=Q(amount__gt=0), name="payment_amount_positive"),
        ]

    def __str__(self):
        return self.number

    @property
    def is_receipt(self) -> bool:
        return self.invoice.kind == InvoiceKind.SALES
