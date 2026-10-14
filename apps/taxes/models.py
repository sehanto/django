from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TenantOwnedModel, money_field
from apps.core.validators import ntpn_validator

from .domain import Month, PayerWithholding


class TaxRate(TenantOwnedModel):
    """A PPN (VAT) rate selectable on invoice lines."""

    name = models.CharField(_("name"), max_length=100)
    rate = models.DecimalField(
        _("rate (%)"), max_digits=5, decimal_places=2,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    dpp_numerator = models.PositiveSmallIntegerField(_("DPP numerator"), default=1, validators=[MinValueValidator(1)])
    dpp_denominator = models.PositiveSmallIntegerField(
        _("DPP denominator"), default=1, validators=[MinValueValidator(1)], help_text=_("Alternative tax base (DPP nilai lain), e.g. 11/12 for non-luxury goods since 2025.")
    )
    # PPN "besaran tertentu" (Art. 9A UU PPN, Faktur Pajak code 05): ``rate`` is then the
    # fixed share of the selling price charged as PPN, e.g. 1.2 for certain farm produce.
    is_specific_amount = models.BooleanField(
        _("PPN besaran tertentu (code 05)"), default=False,
        help_text=_("For deliveries whose PPN is a fixed share of the price (Art. 9A UU PPN), e.g. certain farm produce or used cars: enter that share as the rate, with DPP 1/1."),
    )
    luxury_rate = models.DecimalField(
        _("PPnBM rate (%)"), max_digits=5, decimal_places=2, default=0,
        validators=[MinValueValidator(0), MaxValueValidator(200)],
        help_text=_("Sales tax on luxury goods, charged on the same tax base as the PPN. 0 for none."),
    )
    is_active = models.BooleanField(_("active"), default=True)

    class Meta:
        ordering = ["name"]
        verbose_name = _("tax rate")
        verbose_name_plural = _("tax rates")

    def __str__(self):
        return self.name

    def clean(self):
        if self.dpp_numerator and self.dpp_denominator and self.dpp_numerator > self.dpp_denominator:
            raise ValidationError({"dpp_numerator": _("The DPP numerator cannot be larger than the denominator.")})
        if self.is_specific_amount and (self.dpp_numerator != self.dpp_denominator or self.luxury_rate):
            raise ValidationError({"is_specific_amount": _("PPN besaran tertentu is a share of the full price: use DPP 1/1 and no PPnBM.")})

    @property
    def effective_rate(self) -> Decimal:
        return (self.rate * self.dpp_numerator / self.dpp_denominator).quantize(Decimal("0.01"))


class PphFinalStatus(models.TextChoices):
    POSTED = "posted", _("Recorded")
    VOID = "void", _("Void")


class PphFinalAccrual(TenantOwnedModel):
    """PPh Final expense of one month, recognised at the month's end (accrual basis, SAK EMKM):
    Beban PPh Final against Utang PPh Final, which the deposit later settles. Cancelling voids
    it (its journal entry is reversed); never deleted (taxes migration 0008)."""

    Status = PphFinalStatus

    year = models.PositiveSmallIntegerField(_("year"))
    month = models.PositiveSmallIntegerField(_("month"), choices=Month.choices)
    gross_turnover = money_field(_("gross turnover"))
    taxable_turnover = money_field(_("taxable gross turnover"))
    tax_amount = money_field(_("tax due"))
    journal_entry = models.OneToOneField("ledger.JournalEntry", on_delete=models.PROTECT, related_name="+")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    status = models.CharField(_("status"), max_length=10, choices=PphFinalStatus.choices, default=PphFinalStatus.POSTED)

    class Meta:
        ordering = ["-year", "-month"]
        verbose_name = _("PPh Final expense")
        verbose_name_plural = _("PPh Final expenses")
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "year", "month"], condition=models.Q(status=PphFinalStatus.POSTED),
                name="unique_pph_final_accrual_period",
            ),
        ]

    def __str__(self):
        return f"Beban PPh Final {Month(self.month).tax_period(self.year)}"


class PphFinalPayment(TenantOwnedModel):
    """Monthly PPh Final UMKM deposit (setoran), linked to its journal. Cancelling voids it
    (its journal entry is reversed); the record is never deleted (taxes migration 0006)."""

    Status = PphFinalStatus

    year = models.PositiveSmallIntegerField(_("year"))
    month = models.PositiveSmallIntegerField(_("month"), choices=Month.choices)
    gross_turnover = money_field(_("gross turnover"))
    taxable_turnover = money_field(_("taxable gross turnover"))
    tax_amount = money_field(_("tax due"))
    # PPh Final that buyers withheld from receipts of the month and deposit themselves, less
    # what refunds of the month gave back to them (so it can be negative); the deposit is the
    # rest (tax_amount - withheld).
    withheld = money_field(_("withheld by buyers"))
    paid_on = models.DateField(_("payment date"))
    ntpn = models.CharField(_("NTPN"), max_length=32, blank=True, validators=[ntpn_validator], help_text=_("State revenue transaction number (Nomor Transaksi Penerimaan Negara)."))
    cash_account = models.ForeignKey("ledger.Account", on_delete=models.PROTECT, related_name="+", verbose_name=_("paid from"))
    journal_entry = models.OneToOneField("ledger.JournalEntry", on_delete=models.PROTECT, related_name="+")
    accrual = models.ForeignKey(
        PphFinalAccrual, null=True, blank=True, on_delete=models.PROTECT, related_name="payments",
        help_text="The expense this deposit settles; empty for deposits recorded before expenses were accrued.",
    )
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    status = models.CharField(_("status"), max_length=10, choices=PphFinalStatus.choices, default=PphFinalStatus.POSTED)

    class Meta:
        ordering = ["-year", "-month"]
        verbose_name = _("PPh Final payment")
        verbose_name_plural = _("PPh Final payments")
        constraints = [
            models.UniqueConstraint(  # one recorded deposit per period; voided ones stay as history
                fields=["organization", "year", "month"], condition=models.Q(status=PphFinalStatus.POSTED),
                name="unique_pph_final_period",
            ),
            models.CheckConstraint(
                name="pph_final_withheld_below_tax", condition=models.Q(withheld__lt=models.F("tax_amount")),
            ),
        ]

    def __str__(self):
        return f"PPh Final {Month(self.month).tax_period(self.year)}"

    @property
    def deposited(self) -> Decimal:
        return self.tax_amount - self.withheld


class WithholdingDeposit(TenantOwnedModel):
    """The monthly deposit (setoran) of the income tax withheld from suppliers (PPh 23 or 4(2)):
    it settles the withheld tax payable. Cancelling voids it (its journal entry is reversed);
    never deleted (taxes migration 0012)."""

    Status = PphFinalStatus
    Kind = PayerWithholding

    kind = models.CharField(_("income tax"), max_length=10, choices=PayerWithholding.choices)
    year = models.PositiveSmallIntegerField(_("year"))
    month = models.PositiveSmallIntegerField(_("month"), choices=Month.choices)
    amount = money_field(_("amount"))
    paid_on = models.DateField(_("payment date"))
    ntpn = models.CharField(_("NTPN"), max_length=32, blank=True, validators=[ntpn_validator])
    cash_account = models.ForeignKey("ledger.Account", on_delete=models.PROTECT, related_name="+", verbose_name=_("paid from"))
    journal_entry = models.OneToOneField("ledger.JournalEntry", on_delete=models.PROTECT, related_name="+")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    status = models.CharField(_("status"), max_length=10, choices=PphFinalStatus.choices, default=PphFinalStatus.POSTED)

    class Meta:
        ordering = ["-year", "-month", "kind"]
        verbose_name = _("withholding tax deposit")
        verbose_name_plural = _("withholding tax deposits")
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "kind", "year", "month"], condition=models.Q(status=PphFinalStatus.POSTED),
                name="unique_withholding_deposit_period",
            ),
            models.CheckConstraint(name="withholding_deposit_positive", condition=models.Q(amount__gt=0)),
        ]

    def __str__(self):
        return f"{self.get_kind_display()} {Month(self.month).tax_period(self.year)}"


class ImportDeclaration(TenantOwnedModel):
    """An import of goods (PIB, pemberitahuan impor barang) and the taxes paid at customs on it:
    PPN impor (input PPN, creditable by a PKP), PPnBM, PPh 22 impor (prepaid income tax) and
    import duty. Reported in the SPT Masa PPN as "Dokumen Lain Masukan". The goods themselves are
    a bill from the foreign supplier, without PPN. Cancelling voids it (its journal entry is
    reversed); never deleted (taxes 0015)."""

    Status = PphFinalStatus

    number = models.CharField(_("PIB no."), max_length=50, help_text=_("The registration number of the import declaration."))
    date = models.DateField(_("PIB date"))
    supplier_name = models.CharField(_("foreign supplier"), max_length=200)
    bill = models.ForeignKey(
        "invoicing.Invoice", null=True, blank=True, on_delete=models.PROTECT, related_name="import_declarations",
        verbose_name=_("supplier's bill"),
    )
    customs_value = money_field(
        _("import value (DPP)"), validators=[MinValueValidator(0)], help_text=_("CIF value plus import duty: the base of PPN impor."),
    )
    import_duty = money_field(_("import duty (bea masuk)"), validators=[MinValueValidator(0)])
    vat = money_field(_("PPN impor"), validators=[MinValueValidator(0)])
    luxury_tax = money_field(_("PPnBM impor"), validators=[MinValueValidator(0)])
    pph22 = money_field(_("PPh 22 impor"), validators=[MinValueValidator(0)])
    cost_account = models.ForeignKey(
        "ledger.Account", on_delete=models.PROTECT, related_name="+", verbose_name=_("cost account"),
        help_text=_("Where the import duty and PPnBM go (and the PPN impor, for a business that isn't a PKP)."),
    )
    prepaid_tax_account = models.ForeignKey(
        "ledger.Account", null=True, blank=True, on_delete=models.PROTECT, related_name="+", verbose_name=_("prepaid tax account"),
    )
    cash_account = models.ForeignKey("ledger.Account", on_delete=models.PROTECT, related_name="+", verbose_name=_("paid from"))
    ntpn = models.CharField(_("NTPN"), max_length=32, blank=True, validators=[ntpn_validator])
    journal_entry = models.OneToOneField("ledger.JournalEntry", on_delete=models.PROTECT, related_name="+")
    status = models.CharField(_("status"), max_length=10, choices=PphFinalStatus.choices, default=PphFinalStatus.POSTED)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-date", "-id"]
        verbose_name = _("import declaration (PIB)")
        verbose_name_plural = _("import declarations (PIB)")
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "number"], condition=models.Q(status=PphFinalStatus.POSTED), name="unique_import_declaration",
            ),
        ]

    def __str__(self):
        return f"PIB {self.number}"

    @property
    def total(self) -> Decimal:
        return self.import_duty + self.vat + self.luxury_tax + self.pph22


class SelfDeposit(TenantOwnedModel):
    """Final income tax the business deposits itself on its own income, when the payer didn't
    withhold it (e.g. 10% on rent from a tenant who isn't a withholding agent, or a contractor's
    final PPh): the expense in the month of the income, then its deposit. Reported with a "bukti
    penyetoran sendiri" (BPSP). Cancelling voids it (both entries are reversed); never deleted."""

    Status = PphFinalStatus

    tax_object = models.CharField(_("tax object code"), max_length=9)
    year = models.PositiveSmallIntegerField(_("year"))
    month = models.PositiveSmallIntegerField(_("month"), choices=Month.choices)
    tax_base = money_field(_("tax base"), validators=[MinValueValidator(0)])
    rate = models.DecimalField(_("rate (%)"), max_digits=5, decimal_places=2)
    amount = money_field(_("tax"))
    invoice = models.ForeignKey(
        "invoicing.Invoice", null=True, blank=True, on_delete=models.PROTECT, related_name="self_deposits", verbose_name=_("invoice"),
    )
    document_number = models.CharField(_("document no."), max_length=100)
    document_date = models.DateField(_("document date"))
    paid_on = models.DateField(_("payment date"))
    ntpn = models.CharField(_("NTPN"), max_length=32, blank=True, validators=[ntpn_validator])
    cash_account = models.ForeignKey("ledger.Account", on_delete=models.PROTECT, related_name="+", verbose_name=_("paid from"))
    expense_entry = models.OneToOneField("ledger.JournalEntry", on_delete=models.PROTECT, related_name="+")
    payment_entry = models.OneToOneField("ledger.JournalEntry", on_delete=models.PROTECT, related_name="+")
    slip_number = models.CharField(_("BPSP no."), max_length=50, blank=True)
    status = models.CharField(_("status"), max_length=10, choices=PphFinalStatus.choices, default=PphFinalStatus.POSTED)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-year", "-month", "-id"]
        verbose_name = _("self-deposited income tax")
        verbose_name_plural = _("self-deposited income tax")
        constraints = [models.CheckConstraint(name="self_deposit_positive", condition=models.Q(amount__gt=0))]

    def __str__(self):
        return f"{self.tax_object} {Month(self.month).tax_period(self.year)}"
