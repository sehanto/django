from django.conf import settings
from django.db import models
from django.db.models import Q

from apps.core.models import TenantOwnedModel, TimeStampedModel, money_field

from .domain import AccountCategory, AccountType, NormalBalance, SystemRole, normal_balance_for


class Account(TenantOwnedModel):
    """An entry in the organization's chart of accounts (Bagan Akun)."""

    code = models.CharField("kode", max_length=20)
    name = models.CharField("nama akun", max_length=150)
    category = models.CharField("kategori", max_length=30, choices=AccountCategory.choices)
    is_contra = models.BooleanField(
        "akun kontra", default=False, help_text="Mis. Akumulasi Penyusutan, Prive, Retur Penjualan."
    )
    system_role = models.CharField(
        "peran sistem", max_length=30, choices=SystemRole.choices, null=True, blank=True
    )
    description = models.CharField("keterangan", max_length=255, blank=True)
    is_active = models.BooleanField("aktif", default=True)

    class Meta:
        ordering = ["code"]
        verbose_name = "akun"
        verbose_name_plural = "bagan akun"
        constraints = [
            models.UniqueConstraint(fields=["organization", "code"], name="unique_account_code"),
            models.UniqueConstraint(
                fields=["organization", "system_role"],
                condition=Q(system_role__isnull=False),
                name="unique_account_system_role",
            ),
        ]

    def __str__(self):
        return f"{self.code} · {self.name}"

    @property
    def account_type(self) -> AccountType:
        return AccountCategory(self.category).account_type

    @property
    def normal_balance(self) -> NormalBalance:
        return normal_balance_for(self.category, self.is_contra)

    @property
    def is_cash(self) -> bool:
        return self.category == AccountCategory.CASH_BANK


class JournalEntry(TenantOwnedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draf"
        POSTED = "posted", "Diposting"

    class Source(models.TextChoices):
        MANUAL = "manual", "Jurnal Umum"
        SALES_INVOICE = "sales_invoice", "Faktur Penjualan"
        PURCHASE_INVOICE = "purchase_invoice", "Tagihan Pembelian"
        PAYMENT = "payment", "Pembayaran"
        TAX = "tax", "Pajak"
        REVERSAL = "reversal", "Pembalikan"

    number = models.CharField("nomor", max_length=30)
    date = models.DateField("tanggal")
    description = models.CharField("keterangan", max_length=255)
    reference = models.CharField("referensi", max_length=100, blank=True)
    status = models.CharField("status", max_length=10, choices=Status.choices, default=Status.DRAFT)
    source = models.CharField("sumber", max_length=20, choices=Source.choices, default=Source.MANUAL)
    reversal_of = models.OneToOneField(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="reversed_by",
        verbose_name="membalik jurnal",
    )
    posted_at = models.DateTimeField("waktu posting", null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        ordering = ["-date", "-id"]
        verbose_name = "jurnal"
        verbose_name_plural = "jurnal"
        constraints = [
            models.UniqueConstraint(fields=["organization", "number"], name="unique_journal_number"),
        ]
        indexes = [models.Index(fields=["organization", "status", "date"])]

    def __str__(self):
        return self.number

    @property
    def is_posted(self) -> bool:
        return self.status == self.Status.POSTED

    @property
    def is_reversed(self) -> bool:
        return hasattr(self, "reversed_by")

    @property
    def total(self):
        return sum((line.debit for line in self.lines.all()), start=0)


class JournalLine(TimeStampedModel):
    entry = models.ForeignKey(JournalEntry, on_delete=models.CASCADE, related_name="lines")
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="lines", verbose_name="akun")
    description = models.CharField("keterangan", max_length=255, blank=True)
    debit = money_field("debit")
    credit = money_field("kredit")

    class Meta:
        ordering = ["id"]
        constraints = [
            models.CheckConstraint(
                condition=Q(debit__gte=0, credit__gte=0)
                & ((Q(debit__gt=0) & Q(credit=0)) | (Q(debit=0) & Q(credit__gt=0))),
                name="journal_line_one_sided_positive",
            )
        ]

    def __str__(self):
        return f"{self.account} D{self.debit} K{self.credit}"
