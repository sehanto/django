from django.db import models

from apps.core.models import TenantOwnedModel

from .validators import format_npwp


class Contact(TenantOwnedModel):
    class Kind(models.TextChoices):
        CUSTOMER = "customer", "Pelanggan"
        SUPPLIER = "supplier", "Pemasok"
        BOTH = "both", "Pelanggan & Pemasok"

    kind = models.CharField("jenis", max_length=10, choices=Kind.choices, default=Kind.CUSTOMER)
    name = models.CharField("nama", max_length=200)
    npwp = models.CharField("NPWP", max_length=16, blank=True)
    email = models.EmailField("email", blank=True)
    phone = models.CharField("telepon", max_length=30, blank=True)
    address = models.TextField("alamat", blank=True)
    city = models.CharField("kota/kabupaten", max_length=100, blank=True)
    payment_terms_days = models.PositiveSmallIntegerField("termin pembayaran (hari)", default=30)
    notes = models.TextField("catatan", blank=True)
    is_active = models.BooleanField("aktif", default=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "kontak"
        verbose_name_plural = "kontak"
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
