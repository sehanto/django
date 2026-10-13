from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TenantOwnedModel, money_field
from apps.core.validators import ntpn_validator

from .domain import Month, VatResult, compute_vat


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

    @property
    def effective_rate(self) -> Decimal:
        return (self.rate * self.dpp_numerator / self.dpp_denominator).quantize(Decimal("0.01"))

    def compute(self, base: Decimal) -> VatResult:
        return compute_vat(base, self.rate, self.dpp_numerator, self.dpp_denominator)


class PphFinalStatus(models.TextChoices):
    POSTED = "posted", _("Recorded")
    VOID = "void", _("Void")


class PphFinalPayment(TenantOwnedModel):
    """Monthly PPh Final UMKM deposit (setoran), linked to its journal. Cancelling voids it
    (its journal entry is reversed); the record is never deleted (taxes migration 0006)."""

    Status = PphFinalStatus

    year = models.PositiveSmallIntegerField(_("year"))
    month = models.PositiveSmallIntegerField(_("month"), choices=Month.choices)
    gross_turnover = money_field(_("gross turnover"))
    taxable_turnover = money_field(_("taxable gross turnover"))
    tax_amount = money_field(_("tax due"))
    paid_on = models.DateField(_("payment date"))
    ntpn = models.CharField(_("NTPN"), max_length=32, blank=True, validators=[ntpn_validator], help_text=_("State revenue transaction number (Nomor Transaksi Penerimaan Negara)."))
    cash_account = models.ForeignKey("ledger.Account", on_delete=models.PROTECT, related_name="+", verbose_name=_("paid from"))
    journal_entry = models.OneToOneField("ledger.JournalEntry", on_delete=models.PROTECT, related_name="+")
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
        ]

    def __str__(self):
        return f"PPh Final {Month(self.month).tax_period(self.year)}"
