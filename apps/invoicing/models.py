from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import F, Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.contacts.countries import INDONESIA
from apps.contacts.models import BuyerIdType, VatCollector
from apps.contacts.validators import HEAD_OFFICE, format_npwp, npwp16
from apps.core.models import TenantOwnedModel, TimeStampedModel, money_field
from apps.core.money import ZERO
from apps.core.validators import code_validator, tax_invoice_number_validator

from .coretax_api import ConnectorName, DocumentType, SubmissionStatus
from .domain import InvoiceStatus, LineSpec, TaxSpec
from .facilities import ADDITIONAL_INFO, STAMPS, VatFacility, describe, stamp_text


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


class DeliveryKind(models.TextChoices):
    """Deliveries that take their own Faktur Pajak transaction code."""

    NORMAL = "", _("Normal delivery")
    TOURIST = "06", _("VAT refund for a tourist (code 06)")
    ASSET = "09", _("Asset not originally for sale, Art. 16D (code 09)")


class SpecialDocument(models.TextChoices):
    """Deliveries reported with a document other than a Faktur Pajak (Coretax "Dokumen Lain"):
    exports under a PEB (sales), and purchases whose bill is itself the tax document (dokumen
    tertentu yang dipersamakan dengan faktur, e.g. electricity or telephone bills)."""

    NONE = "", _("None: a Faktur Pajak")
    EXPORT_GOODS = "export_goods", _("Export of goods (PEB)")
    EXPORT_INTANGIBLE = "export_intangible", _("Export of intangible goods")
    EXPORT_SERVICES = "export_services", _("Export of services")
    EQUIVALENT = "equivalent", _("Bill equivalent to a Faktur Pajak (e.g. electricity, telephone)")


EXPORTS = (SpecialDocument.EXPORT_GOODS, SpecialDocument.EXPORT_INTANGIBLE, SpecialDocument.EXPORT_SERVICES)


class Invoice(TenantOwnedModel):
    Status = InvoiceStatus
    VatFacility = VatFacility
    DeliveryKind = DeliveryKind
    SpecialDocument = SpecialDocument

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
    # A PPN facility (Faktur Pajak code 07/08): the lines keep their PPN rate so the faktur
    # shows the PPN, but the buyer isn't charged it (``vat_not_collected``, not in the total).
    vat_facility = models.CharField(
        _("PPN facility"), max_length=2, choices=VatFacility.choices, default=VatFacility.NONE, blank=True,
    )
    facility_info = models.CharField(_("facility (keterangan tambahan)"), max_length=10, blank=True)
    facility_stamp = models.CharField(_("facility stamp (cap fasilitas)"), max_length=10, blank=True)
    supporting_document = models.CharField(
        _("supporting document"), max_length=100, blank=True,
        help_text=_("For a facility, if any: e.g. the SPPB submission number, or PJKEK#number for a special economic zone."),
    )
    supporting_document_date = models.DateField(_("supporting document date"), null=True, blank=True)
    delivery_kind = models.CharField(
        _("type of delivery"), max_length=2, choices=DeliveryKind.choices, default=DeliveryKind.NORMAL, blank=True,
    )
    special_document = models.CharField(
        _("other tax document"), max_length=20, choices=SpecialDocument.choices, default=SpecialDocument.NONE, blank=True,
    )
    special_document_number = models.CharField(
        _("document no."), max_length=100, blank=True,
        help_text=_("The PEB (export declaration) or the bill's own number; it can be added once after issue."),
    )
    special_document_date = models.DateField(_("document date"), null=True, blank=True)
    # Where the sale is made from (its NITKU goes on the faktur); empty for the head office.
    business_place = models.ForeignKey(
        "tenants.BusinessPlace", null=True, blank=True, on_delete=models.PROTECT, related_name="invoices",
        verbose_name=_("place of business"),
    )
    status = models.CharField(_("status"), max_length=15, choices=Status.choices, default=Status.DRAFT)

    subtotal = money_field(_("subtotal"))
    tax_amount = money_field(_("PPN (VAT)"))
    vat_not_collected = money_field(_("PPN not collected (facility)"))
    luxury_tax_amount = money_field(_("PPnBM"))
    total = money_field(_("total"))
    amount_paid = money_field(_("amount paid"))
    # Returns credited against the balance (not the part refunded in cash): with the payments,
    # it settles the invoice. The database checks it against the posted returns (invoicing 0011).
    amount_credited = money_field(_("returns credited"))
    amount_written_off = money_field(_("written off as uncollectible"))

    journal_entry = models.OneToOneField(
        "ledger.JournalEntry", null=True, blank=True, on_delete=models.PROTECT, related_name="invoice"
    )
    issued_at = models.DateTimeField(null=True, blank=True)
    # Parties as they were when the invoice was issued (frozen with it; contacts and the
    # organization stay editable for future invoices). Empty while a draft.
    contact_name = models.CharField(_("contact name"), max_length=200, blank=True, editable=False)
    # Whether the buyer withholds the PPN (contacts.VatCollector), as it was at issue.
    buyer_vat_collector = models.CharField(
        _("buyer collects the PPN"), max_length=12, choices=VatCollector.choices, blank=True, default="", editable=False,
    )
    contact_npwp = models.CharField(_("contact NPWP"), max_length=16, blank=True, editable=False)
    contact_nitku = models.CharField(_("contact NITKU"), max_length=22, blank=True, editable=False)
    contact_id_type = models.CharField(
        _("contact identity document"), max_length=12, choices=BuyerIdType.choices, blank=True, editable=False,
    )
    contact_id_number = models.CharField(_("contact identity document number"), max_length=30, blank=True, editable=False)
    contact_country = models.CharField(_("contact country"), max_length=3, blank=True, editable=False)
    contact_address = models.TextField(_("contact address"), blank=True, editable=False)
    contact_city = models.CharField(_("contact region"), max_length=255, blank=True, editable=False)
    seller_name = models.CharField(_("seller name"), max_length=200, blank=True, editable=False)
    seller_npwp = models.CharField(_("seller NPWP"), max_length=16, blank=True, editable=False)
    seller_nitku = models.CharField(_("seller NITKU"), max_length=22, blank=True, editable=False)
    seller_address = models.TextField(_("seller address"), blank=True, editable=False)
    seller_city = models.CharField(_("seller region"), max_length=255, blank=True, editable=False)
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
    def is_export(self) -> bool:
        return self.special_document in EXPORTS

    @property
    def display_number(self) -> str:
        return self.number or _("DRAFT-%(id)s") % {"id": self.pk}

    def snapshot_parties(self) -> None:
        """Copy the buyer's and the organization's details onto the invoice (at issue)."""
        contact, organization = self.contact, self.organization
        self.contact_name, self.contact_npwp = contact.name, contact.npwp
        self.contact_nitku, self.contact_country = contact.nitku, contact.country
        self.contact_id_type, self.contact_id_number = contact.id_type, contact.id_number
        self.buyer_vat_collector = contact.vat_collector if self.is_sales else VatCollector.NONE
        # contact_city/seller_city hold the region line: kelurahan/desa, kecamatan, kabupaten/kota, province, postcode.
        self.contact_address, self.contact_city = contact.address, contact.region_line
        self.seller_name, self.seller_npwp = organization.name, organization.npwp
        place = self.business_place
        self.seller_address, self.seller_city = organization.address, organization.region_line
        if place is not None and (place.address or place.village_id):
            self.seller_address, self.seller_city = place.address, place.region_line
        self.seller_nitku = place.nitku if place is not None else ""

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
    def party_tin(self) -> str:
        """The buyer's 16-digit NPWP for Coretax, or "" when it has none."""
        tin = npwp16(self._party("contact_npwp", self.contact.npwp))
        return tin if len(tin) == 16 else ""

    @property
    def party_nitku(self) -> str:
        """The buyer's place of business: its branch NITKU, or the head office."""
        tin = self.party_tin
        return (self._party("contact_nitku", self.contact.nitku) or tin + HEAD_OFFICE) if tin else ""

    @property
    def party_id_type(self) -> str:
        return self._party("contact_id_type", self.contact.id_type)

    @property
    def party_id_number(self) -> str:
        return self._party("contact_id_number", self.contact.id_number)

    @property
    def party_has_tin(self) -> bool:
        """An NPWP, or the NIK that serves as one: without either, PPh 21 is 20% higher."""
        return bool(self.party_tin) or (self.party_id_type == BuyerIdType.NATIONAL_ID and len(self.party_id_number) == 16)

    @property
    def party_id_label(self) -> str:
        return BuyerIdType(self.party_id_type).label if self.party_id_type else ""

    @property
    def party_country(self) -> str:
        # Invoices issued before countries were recorded were all to Indonesian buyers.
        return self._party("contact_country", self.contact.country) or INDONESIA

    @property
    def seller_tin_nitku(self) -> str:
        """The seller's place of business: its branch NITKU, or the head office."""
        if self.status == self.Status.DRAFT:
            npwp, nitku = self.organization.npwp, self.business_place.nitku if self.business_place_id else ""
        else:
            npwp, nitku = self.seller_npwp, self.seller_nitku
        return nitku or npwp16(npwp) + HEAD_OFFICE

    @property
    def facility_info_label(self) -> str:
        return describe(ADDITIONAL_INFO, self.vat_facility, self.facility_info)

    @property
    def facility_stamp_label(self) -> str:
        return describe(STAMPS, self.vat_facility, self.facility_stamp)

    @property
    def facility_stamp_text(self) -> str:
        return stamp_text(self.vat_facility, self.facility_stamp)

    @property
    def party_address(self) -> str:
        return self._party("contact_address", self.contact.address)

    @property
    def party_city(self) -> str:
        return self._party("contact_city", self.contact.region_line)

    @property
    def seller(self) -> dict:
        org = self.organization
        if self.status == self.Status.DRAFT:
            place = self.business_place
            if place is not None and (place.address or place.village_id):
                return {"name": org.name, "npwp": org.npwp, "address": place.address, "city": place.region_line}
            return {"name": org.name, "npwp": org.npwp, "address": org.address, "city": org.region_line}
        return {"name": self.seller_name, "npwp": self.seller_npwp, "address": self.seller_address, "city": self.seller_city}

    @property
    def balance_due(self):
        return self.total - self.amount_paid - self.amount_credited - self.amount_written_off

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
    def buyer_withholds_vat(self) -> bool:
        return bool(self.buyer_vat_collector) and bool(self.tax_amount or self.luxury_tax_amount)

    def _posted_payments(self):
        return [p for p in self.payments.all() if p.status == Payment.Status.POSTED]

    def _posted_returns(self):
        return [r for r in self.returns.all() if r.status == ReturnNote.Status.POSTED]

    @property
    def vat_withheld(self):
        """PPN (and PPnBM) the buyer withheld on its payments, less what returns gave back to it."""
        return (
            sum((p.vat_withheld for p in self._posted_payments()), ZERO)
            - sum((r.vat_withheld for r in self._posted_returns()), ZERO)
        )

    @property
    def vat_still_withheld(self):
        """How much more PPN the buyer may withhold: the PPN after returns, less what it withheld."""
        returned = sum((r.tax_amount + r.luxury_tax_amount for r in self._posted_returns()), ZERO)
        return max(self.tax_amount + self.luxury_tax_amount - returned - self.vat_withheld, ZERO)

    @property
    def income_tax_withheld(self):
        """Income tax the buyer withheld on its payments, less what refunds gave back to it."""
        return (
            sum((p.income_tax_withheld for p in self._posted_payments()), ZERO)
            - sum((r.income_tax_withheld for r in self._posted_returns()), ZERO)
        )

    @property
    def is_asset_sale(self) -> bool:
        """Made by disposing of a fixed asset: it stands or falls with that disposal."""
        return self.disposed_assets.exists() if self.pk else False

    @property
    def can_return(self) -> bool:
        return self.status in (self.Status.ISSUED, self.Status.PARTIAL, self.Status.PAID) and not self.is_asset_sale

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
    tax_specific_amount = models.BooleanField(_("PPN besaran tertentu when issued"), null=True, blank=True, editable=False)
    tax_luxury_rate = models.DecimalField(_("PPnBM rate when issued (%)"), max_digits=5, decimal_places=2, null=True, blank=True, editable=False)
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
            rate = self.tax_rate
            tax = TaxSpec(self.tax_rate_id, rate.rate, rate.dpp_numerator, rate.dpp_denominator, rate.luxury_rate)
        return LineSpec(self.quantity, self.unit_price, self.discount_percent, tax)


# Invoice list filter: every stored status plus the derived "overdue" (open and past due).
InvoiceStatusFilter = models.TextChoices(
    "InvoiceStatusFilter",
    [(status.name, (status.value, status.label)) for status in Invoice.Status] + [("OVERDUE", ("overdue", _("Overdue")))],
)


class IncomeTaxWithheld(models.TextChoices):
    """Income tax a buyer withholds from what it pays (it deposits the tax and gives a bukti potong)."""

    NONE = "", _("None")
    PPH_22 = "pph22", _("PPh 22 (e.g. 1.5% by a government treasurer)")
    # xgettext:no-python-format
    PPH_23 = "pph23", _("PPh 23 (2% on services)")
    PPH_FINAL = "pph_final", _("PPh Final 0.5% (PP 55/2022, with a Surat Keterangan)")
    # xgettext:no-python-format
    PPH_4_2 = "pph4_2", _("PPh 4(2) final (e.g. 10% on rent of land or buildings)")
    PPH_21 = "pph21", _("PPh 21 (fee to an individual who is not an employee)")
    # xgettext:no-python-format
    PPH_26 = "pph26", _("PPh 26 (supplier abroad, 20% or the treaty rate)")


# Which the customer withholds from us (receipts), and which we withhold from suppliers (disbursements).
RECEIPT_WITHHOLDING = (IncomeTaxWithheld.PPH_22, IncomeTaxWithheld.PPH_23, IncomeTaxWithheld.PPH_FINAL)
PAYER_WITHHOLDING = (IncomeTaxWithheld.PPH_23, IncomeTaxWithheld.PPH_4_2, IncomeTaxWithheld.PPH_21, IncomeTaxWithheld.PPH_26)


class Payment(TenantOwnedModel):
    IncomeTax = IncomeTaxWithheld

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
    # Part of ``amount`` the buyer withheld as PPN and deposits itself (a VAT collector); the
    # rest is the cash received.
    vat_withheld = money_field(_("PPN withheld by the buyer"), validators=[MinValueValidator(0)])
    # Part of ``amount`` the buyer withheld as income tax: prepaid tax (PPh 22/23) or part of
    # the month's PPh Final, which the buyer deposits for us.
    income_tax_kind = models.CharField(
        _("income tax withheld"), max_length=10, choices=IncomeTaxWithheld.choices, default=IncomeTaxWithheld.NONE, blank=True,
    )
    income_tax_withheld = money_field(_("income tax withheld by the buyer"), validators=[MinValueValidator(0)])
    income_tax_account = models.ForeignKey(
        "ledger.Account", null=True, blank=True, on_delete=models.PROTECT, related_name="+", verbose_name=_("prepaid tax account"),
    )
    withholding_slip = models.CharField(
        _("withholding slip no."), max_length=50, blank=True, help_text=_("The bukti potong number from the buyer."),
    )
    # When we withhold from a supplier: the gross amount the tax is on and its rate (the bukti potong).
    income_tax_base = money_field(_("income tax base"), validators=[MinValueValidator(0)])
    income_tax_rate = models.DecimalField(
        _("income tax rate (%)"), max_digits=5, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(Decimal("0.01")), MaxValueValidator(100)],
    )
    income_tax_object = models.CharField(
        _("tax object code"), max_length=9, blank=True, help_text=_("Kode objek pajak on the bukti potong, e.g. 24-104-01."),
    )
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
            models.CheckConstraint(condition=Q(vat_withheld__gte=0, vat_withheld__lte=F("amount")), name="payment_vat_withheld_within_amount"),
            models.CheckConstraint(
                name="payment_income_tax_withheld_within_amount",
                condition=Q(income_tax_withheld__gte=0, income_tax_withheld__lte=F("amount") - F("vat_withheld"))
                & (
                    Q(income_tax_kind="", income_tax_withheld=0) | (~Q(income_tax_kind="") & Q(income_tax_withheld__gt=0))
                    # PPh 26 at a treaty's 0% rate: nothing withheld, reported all the same (BPNR)
                    | Q(income_tax_kind=IncomeTaxWithheld.PPH_26, income_tax_withheld=0, income_tax_rate=0)
                ),
            ),
        ]

    def __str__(self):
        return self.number

    @property
    def is_receipt(self) -> bool:
        return self.invoice.kind == InvoiceKind.SALES

    @property
    def cash_received(self):
        return self.amount - self.vat_withheld - self.income_tax_withheld


class ReceivableWriteOff(TenantOwnedModel):
    """A receivable written off as uncollectible (SAK EMKM 8.11(b)): what is still owed on a
    sales invoice leaves the receivables and is a loss (8.13). SAK EMKM allows no allowance for
    doubtful debts (8.10), so a receivable stays at its full amount until it is written off.

    Final once posted (invoicing 0028); a mistake is undone by voiding it, which reverses its
    journal entry and reopens the invoice."""

    class Status(models.TextChoices):
        POSTED = "posted", _("Recorded")
        VOID = "void", _("Void")

    status = models.CharField(_("status"), max_length=10, choices=Status.choices, default=Status.POSTED)
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="write_offs", verbose_name=_("invoice"))
    date = models.DateField(_("date"))
    amount = money_field(_("amount written off"))
    reason = models.CharField(_("reason"), max_length=255, help_text=_("Why it can no longer be collected, e.g. the customer went bankrupt."))
    journal_entry = models.OneToOneField("ledger.JournalEntry", on_delete=models.PROTECT, related_name="write_off")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-date", "-id"]
        verbose_name = _("receivable written off")
        verbose_name_plural = _("receivables written off")
        constraints = [models.CheckConstraint(condition=Q(amount__gt=0), name="write_off_amount_positive")]

    def __str__(self):
        return f"{self.invoice} {self.amount}"


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
    # PPN of the return that the buyer (a VAT collector) had withheld and deposited: it is
    # neither credited nor refunded, the buyer settles it with the tax office.
    vat_withheld = money_field(_("PPN withheld by the buyer"))
    vat_not_collected = money_field(_("PPN not collected (facility)"))
    luxury_tax_amount = money_field(_("PPnBM"))
    # Income tax the buyer withheld on the part refunded: the buyer corrects its withholding
    # slip, so it comes off the prepaid tax (or the PPh Final settled) instead of being paid out.
    income_tax_kind = models.CharField(
        _("income tax withheld"), max_length=10, choices=IncomeTaxWithheld.choices, default=IncomeTaxWithheld.NONE, blank=True,
    )
    income_tax_withheld = money_field(_("income tax the buyer withheld"))
    income_tax_account = models.ForeignKey(
        "ledger.Account", null=True, blank=True, on_delete=models.PROTECT, related_name="+", verbose_name=_("prepaid tax account"),
    )
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
                condition=Q(
                    total__gt=0, credited__gte=0, refunded__gte=0, income_tax_withheld__gte=0,
                    vat_withheld__gte=0, vat_withheld__lte=F("tax_amount") + F("luxury_tax_amount"),
                    total=F("credited") + F("refunded") + F("vat_withheld") + F("income_tax_withheld"),
                ),
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


class CoretaxConnection(TimeStampedModel):
    """How an organization reaches Coretax DJP through its tax application provider (PJAP).
    The access token is stored encrypted (``apps.core.crypto``)."""

    organization = models.OneToOneField("tenants.Organization", on_delete=models.CASCADE, related_name="coretax_connection")
    connector = models.CharField(_("connection"), max_length=10, choices=ConnectorName.choices)
    api_url = models.URLField(_("API address"), blank=True, help_text=_("The HTTPS address your provider gave you."))
    token = models.TextField(blank=True, editable=False)  # encrypted
    is_active = models.BooleanField(_("active"), default=True)

    class Meta:
        verbose_name = _("Coretax connection")
        verbose_name_plural = _("Coretax connections")

    def __str__(self):
        return f"{self.organization} ({self.get_connector_display()})"


class CoretaxSubmission(TimeStampedModel):
    """One import file sent to Coretax: its provider reference, status and per-faktur results.
    ``organization`` is empty for the platform's own fakturs (subscription invoices)."""

    Status = SubmissionStatus
    DocumentType = DocumentType

    organization = models.ForeignKey("tenants.Organization", null=True, blank=True, on_delete=models.CASCADE, related_name="+")
    document_type = models.CharField(_("document"), max_length=10, choices=DocumentType.choices)
    label = models.CharField(_("contents"), max_length=100)
    connector = models.CharField(_("connection"), max_length=10, choices=ConnectorName.choices)
    reference = models.CharField(_("provider reference"), max_length=64)
    status = models.CharField(_("status"), max_length=10, choices=SubmissionStatus.choices, default=SubmissionStatus.PENDING)
    results = models.JSONField(default=list, blank=True)
    # eBupot: what each record (by position) is a bukti potong of, as [source, id].
    records = models.JSONField(default=list, blank=True)
    invoices = models.ManyToManyField(Invoice, blank=True, related_name="coretax_submissions")
    returns = models.ManyToManyField(ReturnNote, blank=True, related_name="coretax_submissions")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("Coretax submission")
        verbose_name_plural = _("Coretax submissions")

    def __str__(self):
        return f"{self.get_document_type_display()} {self.label} ({self.get_status_display()})"

    @property
    def errors(self) -> list[dict]:
        return [item for item in self.results if item.get("error")]
