from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.contacts.validators import npwp_format_validator
from apps.core.models import TimeStampedModel, money_field
from apps.core.validators import nib_validator, phone_validator

from .roles import Role


class Plan(TimeStampedModel):
    """Subscription tier. ``None`` limits mean unlimited."""

    code = models.SlugField(_("code"), unique=True)
    name = models.CharField(_("name"), max_length=50)
    monthly_price = money_field(_("monthly price"), help_text=_("Excluding PPN."), validators=[MinValueValidator(0)])
    yearly_price = money_field(_("yearly price"), help_text=_("Excluding PPN."), validators=[MinValueValidator(0)])
    max_users = models.PositiveIntegerField(_("max. users"), null=True, blank=True)
    max_invoices_per_month = models.PositiveIntegerField(_("max. invoices per month"), null=True, blank=True)
    is_default = models.BooleanField(_("default plan"), default=False)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order"]
        verbose_name = _("subscription plan")
        verbose_name_plural = _("subscription plans")

    def __str__(self):
        return self.name


class InventoryCostFormula(models.TextChoices):
    """The cost formulas SAK EMKM allows for inventory."""

    FIFO = "fifo", _("First in, first out (FIFO)")
    WEIGHTED_AVERAGE = "weighted_average", _("Weighted average")


class DepreciationMethod(models.TextChoices):
    """The depreciation methods SAK EMKM allows for fixed assets."""

    STRAIGHT_LINE = "straight_line", _("Straight line")
    DECLINING_BALANCE = "declining_balance", _("Declining balance")


class Organization(TimeStampedModel):
    InventoryCostFormula = InventoryCostFormula
    DepreciationMethod = DepreciationMethod

    class LegalForm(models.TextChoices):
        PERORANGAN = "perorangan", _("Sole proprietorship / UD")
        PT_PERORANGAN = "pt_perorangan", _("Individual limited company (PT Perorangan)")
        CV = "cv", _("CV (limited partnership)")
        FIRMA = "firma", _("General partnership (Firma)")
        KOPERASI = "koperasi", _("Cooperative (Koperasi)")
        PT = "pt", _("PT (limited liability company)")

    name = models.CharField(_("business name"), max_length=200)
    legal_form = models.CharField(_("legal form"), max_length=20, choices=LegalForm.choices)
    npwp = models.CharField(
        _("tax ID (NPWP)"), max_length=16, blank=True, validators=[npwp_format_validator],
        help_text=_("15 or 16 digits without punctuation."),
    )
    nib = models.CharField(
        _("business ID (NIB)"), max_length=13, blank=True, validators=[nib_validator],
        help_text=_("Nomor Induk Berusaha, issued through OSS."),
    )
    address = models.TextField(_("address"), max_length=500, blank=True)
    city = models.CharField(_("city/regency"), max_length=100, blank=True)
    phone = models.CharField(_("phone"), max_length=30, blank=True, validators=[phone_validator])
    email = models.EmailField(_("email"), blank=True)

    is_pkp = models.BooleanField(
        _("VAT-registered business (PKP)"),
        default=False,
        help_text=_("Enable if the business is registered as a PKP and must charge PPN (VAT)."),
    )
    uses_pph_final = models.BooleanField(
        _("uses MSME final income tax (PPh Final)"),
        default=True,
        # xgettext:no-python-format
        help_text=_("PP 55/2022: 0.5% final income tax on gross turnover."),
    )
    require_two_factor = models.BooleanField(
        _("require two-factor authentication"),
        default=False,
        help_text=_("Every member must use two-factor authentication to open this organization."),
    )
    inventory_cost_formula = models.CharField(
        _("inventory cost formula"), max_length=20, choices=InventoryCostFormula.choices,
        default=InventoryCostFormula.WEIGHTED_AVERAGE,
        help_text=_("How the cost of inventory sold is determined (SAK EMKM). Stated in the notes to the financial statements."),
    )
    depreciation_method = models.CharField(
        _("depreciation method"), max_length=20, choices=DepreciationMethod.choices,
        default=DepreciationMethod.STRAIGHT_LINE,
        help_text=_("How fixed assets other than land are depreciated (SAK EMKM). Stated in the notes to the financial statements."),
    )
    additional_notes = models.TextField(
        _("additional information for the notes"), max_length=5000, blank=True,
        help_text=_("Material matters the financial statements don't show, e.g. commitments, guarantees or events after the reporting date. Printed in the notes to the financial statements."),
    )
    lock_date = models.DateField(
        _("period lock date"),
        null=True,
        blank=True,
        help_text=_("Transactions on or before this date cannot be changed."),
    )

    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="organizations", verbose_name=_("plan"))
    plan_paid_until = models.DateField(
        _("plan paid until"), null=True, blank=True, help_text=_("Last day covered by payment; empty for the free plan.")
    )
    renewal_reminder_sent_for = models.DateField(null=True, blank=True, editable=False)
    members = models.ManyToManyField(settings.AUTH_USER_MODEL, through="Membership", related_name="organizations")

    class Meta:
        ordering = ["name"]
        verbose_name = _("organization")
        verbose_name_plural = _("organizations")

    def __str__(self):
        return self.name

    @property
    def is_individual_taxpayer(self) -> bool:
        """Wajib Pajak Orang Pribadi gets the Rp500 juta PPh Final threshold."""
        return self.legal_form == self.LegalForm.PERORANGAN


class Membership(TimeStampedModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memberships")
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="memberships")
    Role = Role  # so templates can compare ``membership.role == membership.Role.OWNER``

    role = models.CharField(_("role"), max_length=20, choices=Role.choices, default=Role.VIEWER)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "organization"], name="unique_membership"),
        ]
        verbose_name = _("member")
        verbose_name_plural = _("members")

    def __str__(self):
        return f"{self.user} @ {self.organization} ({self.get_role_display()})"

    def has_permission(self, permission) -> bool:
        return Role(self.role).allows(permission)
