from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from apps.core.models import TenantOwnedModel, money_field

from .domain import VatResult, compute_vat


class TaxRate(TenantOwnedModel):
    """A PPN (VAT) rate selectable on invoice lines."""

    name = models.CharField("nama", max_length=100)
    rate = models.DecimalField(
        "tarif (%)", max_digits=5, decimal_places=2,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    dpp_numerator = models.PositiveSmallIntegerField("pembilang DPP", default=1)
    dpp_denominator = models.PositiveSmallIntegerField(
        "penyebut DPP", default=1, help_text="DPP nilai lain, mis. 11/12 untuk barang non-mewah sejak 2025."
    )
    is_active = models.BooleanField("aktif", default=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "tarif pajak"
        verbose_name_plural = "tarif pajak"

    def __str__(self):
        return self.name

    @property
    def effective_rate(self) -> Decimal:
        return (self.rate * self.dpp_numerator / self.dpp_denominator).quantize(Decimal("0.01"))

    def compute(self, base: Decimal) -> VatResult:
        return compute_vat(base, self.rate, self.dpp_numerator, self.dpp_denominator)


class PphFinalPayment(TenantOwnedModel):
    """Monthly PPh Final UMKM deposit (setoran), linked to its journal."""

    year = models.PositiveSmallIntegerField("tahun")
    month = models.PositiveSmallIntegerField("bulan", validators=[MinValueValidator(1), MaxValueValidator(12)])
    gross_turnover = money_field("peredaran bruto")
    taxable_turnover = money_field("peredaran bruto kena pajak")
    tax_amount = money_field("PPh terutang")
    paid_on = models.DateField("tanggal setor")
    ntpn = models.CharField("NTPN", max_length=32, blank=True, help_text="Nomor Transaksi Penerimaan Negara.")
    cash_account = models.ForeignKey("ledger.Account", on_delete=models.PROTECT, related_name="+", verbose_name="dibayar dari")
    journal_entry = models.OneToOneField("ledger.JournalEntry", on_delete=models.PROTECT, related_name="+")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-year", "-month"]
        verbose_name = "setoran PPh Final"
        verbose_name_plural = "setoran PPh Final"
        constraints = [
            models.UniqueConstraint(fields=["organization", "year", "month"], name="unique_pph_final_period"),
        ]

    def __str__(self):
        return f"PPh Final {self.month:02d}/{self.year}"
