"""Indonesian administrative regions (Kemendagri codes) and postcodes: the predefined values of
every Indonesian address. Shared reference data, not tenant data: loaded by ``manage.py
load_regions`` (owner connection; the app role may only read these tables), never edited in the
app. Codes that Kemendagri retires are deactivated, never deleted, so addresses keep them."""

from django.core.validators import RegexValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

postal_code_validator = RegexValidator(r"^\d{5}$", _("A postcode has 5 digits."))


class Region(models.Model):
    code = models.CharField(_("Kemendagri code"), max_length=13, primary_key=True)
    name = models.CharField(_("name"), max_length=100)
    is_active = models.BooleanField(_("active"), default=True)

    class Meta:
        abstract = True
        ordering = ["name", "code"]

    def __str__(self):
        return self.name


class Province(Region):
    class Meta(Region.Meta):
        verbose_name = _("province")
        verbose_name_plural = _("provinces")
        constraints = [models.CheckConstraint(condition=models.Q(code__regex=r"^\d{2}$"), name="province_code_format")]


class Regency(Region):
    """Kabupaten or kota."""

    province = models.ForeignKey(Province, on_delete=models.PROTECT, related_name="regencies")

    class Meta(Region.Meta):
        verbose_name = _("regency/city")
        verbose_name_plural = _("regencies/cities")
        constraints = [models.CheckConstraint(condition=models.Q(code__regex=r"^\d{2}\.\d{2}$"), name="regency_code_format")]


class District(Region):
    """Kecamatan."""

    regency = models.ForeignKey(Regency, on_delete=models.PROTECT, related_name="districts")

    class Meta(Region.Meta):
        verbose_name = _("district")
        verbose_name_plural = _("districts")
        constraints = [models.CheckConstraint(condition=models.Q(code__regex=r"^\d{2}\.\d{2}\.\d{2}$"), name="district_code_format")]


class Village(Region):
    """Kelurahan or desa, with its postcode."""

    district = models.ForeignKey(District, on_delete=models.PROTECT, related_name="villages")
    postal_code = models.CharField(_("postcode"), max_length=5, validators=[postal_code_validator])

    class Meta(Region.Meta):
        verbose_name = _("village")
        verbose_name_plural = _("villages")
        constraints = [
            models.CheckConstraint(condition=models.Q(code__regex=r"^\d{2}\.\d{2}\.\d{2}\.\d{4}$"), name="village_code_format"),
            models.CheckConstraint(condition=models.Q(postal_code__regex=r"^\d{5}$"), name="village_postal_code_format"),
        ]
        indexes = [models.Index(fields=["postal_code"])]

    @property
    def is_kelurahan(self) -> bool:
        """Kemendagri numbers kelurahan 1xxx and desa 2xxx."""
        return self.code[9] == "1"

    @property
    def region_line(self) -> str:
        """e.g. "Jatake, Pagedangan, Kabupaten Tangerang, Banten 15330"."""
        district = self.district
        regency = district.regency
        return f"{self.name}, {district.name}, {regency.name}, {regency.province.name} {self.postal_code}"


class Address(models.Model):
    """An Indonesian address: the street line typed in, the region chosen from the Kemendagri
    list (the village fixes the district, regency, province and postcode)."""

    address = models.TextField(_("street address"), max_length=500, blank=True, help_text=_("Street, number, RT/RW and building; for an address abroad, all of it."))
    village = models.ForeignKey(
        Village, on_delete=models.PROTECT, null=True, blank=True, related_name="+", verbose_name=_("kelurahan/desa"),
    )

    class Meta:
        abstract = True

    @property
    def region_line(self) -> str:
        return self.village.region_line if self.village_id else ""

    @property
    def regency_name(self) -> str:
        """The kabupaten/kota, for lists."""
        return self.village.district.regency.name if self.village_id else ""

    @property
    def postal_code(self) -> str:
        return self.village.postal_code if self.village_id else ""

    @property
    def full_address(self) -> str:
        return ", ".join(part for part in (" ".join(self.address.split()), self.region_line) if part)
