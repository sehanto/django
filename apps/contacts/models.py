from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TenantOwnedModel

from .validators import format_npwp


class Contact(TenantOwnedModel):
    class Kind(models.TextChoices):
        CUSTOMER = "customer", _("Customer")
        SUPPLIER = "supplier", _("Supplier")
        BOTH = "both", _("Customer & supplier")

    kind = models.CharField(_("type"), max_length=10, choices=Kind.choices, default=Kind.CUSTOMER)
    name = models.CharField(_("name"), max_length=200)
    npwp = models.CharField(_("tax ID (NPWP)"), max_length=16, blank=True)
    email = models.EmailField(_("email"), blank=True)
    phone = models.CharField(_("phone"), max_length=30, blank=True)
    address = models.TextField(_("address"), blank=True)
    city = models.CharField(_("city/regency"), max_length=100, blank=True)
    payment_terms_days = models.PositiveSmallIntegerField(_("payment terms (days)"), default=30)
    notes = models.TextField(_("notes"), blank=True)
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
