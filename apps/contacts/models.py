from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TenantOwnedModel
from apps.core.validators import document_number_validator, identifier_validator, phone_validator
from apps.regions.models import Address

from .countries import COUNTRY_CODES, INDONESIA
from .validators import format_npwp, nitku_validator, npwp_format_validator, validate_nitku_of


class VatCollector(models.TextChoices):
    """Buyers appointed to collect (withhold) the PPN on what they buy: they pay the seller
    the price without PPN and deposit the PPN themselves (Faktur Pajak code 02 or 03)."""

    NONE = "", _("Not a VAT collector")
    GOVERNMENT = "government", _("Government treasurer (code 02)")
    OTHER = "other", _("Other appointed VAT collector, e.g. a BUMN (code 03)")


class BuyerIdType(models.TextChoices):
    """How a buyer without an NPWP is identified on a Faktur Pajak (values are DJP's codes)."""

    NONE = "", _("None: the NPWP identifies the buyer")
    NATIONAL_ID = "National ID", _("NIK (Indonesian identity card)")
    PASSPORT = "Passport", _("Passport")
    OTHER = "Other ID", _("Other identity document")


def validate_country_code(value: str) -> None:
    if value not in COUNTRY_CODES:
        raise ValidationError(_("Enter a three-letter country code, e.g. IDN, SGP or MYS."))


class Contact(TenantOwnedModel, Address):
    VatCollector = VatCollector
    BuyerIdType = BuyerIdType

    class Kind(models.TextChoices):
        CUSTOMER = "customer", _("Customer")
        SUPPLIER = "supplier", _("Supplier")
        BOTH = "both", _("Customer & supplier")

    kind = models.CharField(_("type"), max_length=10, choices=Kind.choices, default=Kind.CUSTOMER)
    name = models.CharField(_("name"), max_length=200)
    npwp = models.CharField(_("tax ID (NPWP)"), max_length=16, blank=True, validators=[npwp_format_validator])
    # Coretax identifies a place of business by its NITKU; empty means the head office.
    nitku = models.CharField(
        _("NITKU (place of business)"), max_length=22, blank=True, validators=[nitku_validator],
        help_text=_("Only for a branch: its 22-digit NITKU. Empty means the head office (the NPWP followed by 000000)."),
    )
    id_type = models.CharField(
        _("identity document"), max_length=12, choices=BuyerIdType.choices, default=BuyerIdType.NONE, blank=True,
        help_text=_("For a customer without an NPWP, the document that identifies them on a Faktur Pajak."),
    )
    id_number = models.CharField(
        _("identity document number"), max_length=30, blank=True,
        validators=[identifier_validator],
    )
    country = models.CharField(
        _("country"), max_length=3, default=INDONESIA, validators=[validate_country_code],
        help_text=_("Three-letter code as used by Coretax, e.g. IDN, SGP or MYS."),
    )
    # A supplier abroad: what the PPh 26 bukti potong (BPNR) needs.
    foreign_tin = models.CharField(_("foreign tax ID"), max_length=30, blank=True, validators=[identifier_validator], help_text=_("A supplier abroad's tax ID in its country."))
    treaty_document = models.CharField(
        _("certificate of residence no."), max_length=50, blank=True, validators=[document_number_validator],
        help_text=_("A supplier abroad's DGT form (SKD), which allows a tax treaty's lower PPh 26 rate."),
    )
    email = models.EmailField(_("email"), blank=True)
    phone = models.CharField(_("phone"), max_length=30, blank=True, validators=[phone_validator])
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

    def clean(self):
        super().clean()
        errors = {}
        try:
            validate_nitku_of(self.nitku, self.npwp)
        except ValidationError as exc:
            errors["nitku"] = exc
        if self.id_type and not self.id_number:
            errors["id_number"] = _("Enter the number of the identity document.")
        elif self.id_type == BuyerIdType.NATIONAL_ID and not (self.id_number.isdigit() and len(self.id_number) == 16):
            errors["id_number"] = _("A NIK has 16 digits.")
        elif self.id_number and not self.id_type:
            errors["id_type"] = _("Choose the type of identity document.")
        if errors:
            raise ValidationError(errors)

    @property
    def formatted_npwp(self) -> str:
        return format_npwp(self.npwp) if self.npwp else ""

    @property
    def is_customer(self) -> bool:
        return self.kind in (self.Kind.CUSTOMER, self.Kind.BOTH)

    @property
    def is_supplier(self) -> bool:
        return self.kind in (self.Kind.SUPPLIER, self.Kind.BOTH)
