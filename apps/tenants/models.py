from django.conf import settings
from django.db import models

from apps.core.models import TimeStampedModel, money_field

from .roles import Role


class Plan(TimeStampedModel):
    """Subscription tier. ``None`` limits mean unlimited."""

    code = models.SlugField("kode", unique=True)
    name = models.CharField("nama", max_length=50)
    monthly_price = money_field("harga per bulan")
    max_users = models.PositiveIntegerField("maks. pengguna", null=True, blank=True)
    max_invoices_per_month = models.PositiveIntegerField("maks. faktur/bulan", null=True, blank=True)
    is_default = models.BooleanField("paket default", default=False)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order"]
        verbose_name = "paket langganan"
        verbose_name_plural = "paket langganan"

    def __str__(self):
        return self.name


class Organization(TimeStampedModel):
    class LegalForm(models.TextChoices):
        PERORANGAN = "perorangan", "Usaha Perorangan / UD"
        PT_PERORANGAN = "pt_perorangan", "PT Perorangan"
        CV = "cv", "CV (Persekutuan Komanditer)"
        FIRMA = "firma", "Firma"
        KOPERASI = "koperasi", "Koperasi"
        PT = "pt", "PT (Perseroan Terbatas)"

    name = models.CharField("nama usaha", max_length=200)
    legal_form = models.CharField("bentuk usaha", max_length=20, choices=LegalForm.choices)
    npwp = models.CharField("NPWP", max_length=16, blank=True, help_text="15 atau 16 digit tanpa tanda baca.")
    nib = models.CharField("NIB", max_length=13, blank=True, help_text="Nomor Induk Berusaha (OSS).")
    address = models.TextField("alamat", blank=True)
    city = models.CharField("kota/kabupaten", max_length=100, blank=True)
    phone = models.CharField("telepon", max_length=30, blank=True)
    email = models.EmailField("email", blank=True)

    is_pkp = models.BooleanField(
        "Pengusaha Kena Pajak (PKP)",
        default=False,
        help_text="Aktifkan jika usaha telah dikukuhkan sebagai PKP dan wajib memungut PPN.",
    )
    uses_pph_final = models.BooleanField(
        "menggunakan PPh Final UMKM",
        default=True,
        help_text="PP 55/2022: PPh final 0,5% dari peredaran bruto.",
    )
    lock_date = models.DateField(
        "tanggal kunci periode",
        null=True,
        blank=True,
        help_text="Transaksi pada atau sebelum tanggal ini tidak dapat diubah.",
    )

    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="organizations", verbose_name="paket")
    members = models.ManyToManyField(settings.AUTH_USER_MODEL, through="Membership", related_name="organizations")

    class Meta:
        ordering = ["name"]
        verbose_name = "organisasi"
        verbose_name_plural = "organisasi"

    def __str__(self):
        return self.name

    @property
    def is_individual_taxpayer(self) -> bool:
        """Wajib Pajak Orang Pribadi gets the Rp500 juta PPh Final threshold."""
        return self.legal_form == self.LegalForm.PERORANGAN


class Membership(TimeStampedModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memberships")
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="memberships")
    role = models.CharField("peran", max_length=20, choices=Role.choices, default=Role.VIEWER)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "organization"], name="unique_membership"),
        ]
        verbose_name = "anggota"
        verbose_name_plural = "anggota"

    def __str__(self):
        return f"{self.user} @ {self.organization} ({self.get_role_display()})"

    def has_permission(self, permission) -> bool:
        return Role(self.role).allows(permission)
