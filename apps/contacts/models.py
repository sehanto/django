from django.conf import settings
from django.core.validators import MaxValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TenantOwnedModel

from apps.core.validators import phone_validator

from .validators import format_npwp, npwp_format_validator


class VatCollector(models.TextChoices):
    """Buyers appointed to collect (withhold) the PPN on what they buy: they pay the seller
    the price without PPN and deposit the PPN themselves (Faktur Pajak code 02 or 03)."""

    NONE = "", _("Not a VAT collector")
    GOVERNMENT = "government", _("Government treasurer (code 02)")
    OTHER = "other", _("Other appointed VAT collector, e.g. a BUMN (code 03)")


class Contact(TenantOwnedModel):
    VatCollector = VatCollector

    class Kind(models.TextChoices):
        CUSTOMER = "customer", _("Customer")
        SUPPLIER = "supplier", _("Supplier")
        BOTH = "both", _("Customer & supplier")

    kind = models.CharField(_("type"), max_length=10, choices=Kind.choices, default=Kind.CUSTOMER)
    name = models.CharField(_("name"), max_length=200)
    npwp = models.CharField(_("tax ID (NPWP)"), max_length=16, blank=True, validators=[npwp_format_validator])
    email = models.EmailField(_("email"), blank=True)
    phone = models.CharField(_("phone"), max_length=30, blank=True, validators=[phone_validator])
    address = models.TextField(_("address"), max_length=500, blank=True)
    city = models.CharField(_("city/regency"), max_length=100, blank=True)
    payment_terms_days = models.PositiveSmallIntegerField(
        _("payment terms (days)"), default=30, validators=[MaxValueValidator(365)]
    )
    document_language = models.CharField(
        _("document language"),
        max_length=10,
        choices=settings.LANGUAGES,
        default=settings.LANGUAGE_CODE,
        help_text=_("Language of the invoices and bills printed for this contact."),
    )
    vat_collector = models.CharField(
        _("VAT collector"), max_length=12, choices=VatCollector.choices, default=VatCollector.NONE, blank=True,
        help_text=_("For customers appointed to withhold PPN: they pay without the PPN and deposit it themselves."),
    )
    notes = models.TextField(_("notes"), max_length=2000, blank=True)
    is_active = models.BooleanField(_("active"), default=True)

    class Meta:
        ordering = ["name"]
        verbose_name = _("contact")
        verbose_name_plural = _("contacts")
        indexes = [models.Index(fields=["organization", "kind"])]

    def __str__(self):
        return self.name

    @property
    def formatted_npwp(self) -> str:
        return format_npwp(self.npwp) if self.npwp else ""

    @property
    def is_customer(self) -> bool:
        return self.kind in (self.Kind.CUSTOMER, self.Kind.BOTH)

    @property
    def is_supplier(self) -> bool:
        return self.kind in (self.Kind.SUPPLIER, self.Kind.BOTH)
