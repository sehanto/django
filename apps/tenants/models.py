from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.assets.domain import DepreciationMethod
from apps.inventory.domain import CostFormula as InventoryCostFormula
from apps.contacts.validators import nitku_validator, npwp_format_validator, validate_nitku_of
from apps.core.models import TenantOwnedModel, TimeStampedModel, money_field
from apps.core.validators import nib_validator, phone_validator
from apps.regions.models import Address
from apps.taxes.domain import EARLIEST_PPH_FINAL_YEAR, pph_final_last_year

from .domain import PlanFeature, SubscriptionState, subscription_state
from .roles import Role


class Plan(TimeStampedModel):
    """Subscription tier (Starter → Growth → Pro): limits (``None`` means unlimited) and features."""

    Feature = PlanFeature

    code = models.SlugField(_("code"), unique=True)
    name = models.CharField(_("name"), max_length=50)
    monthly_price = money_field(_("monthly price"), help_text=_("Excluding PPN."), validators=[MinValueValidator(0)])
    yearly_price = money_field(_("yearly price"), help_text=_("Excluding PPN."), validators=[MinValueValidator(0)])
    max_users = models.PositiveIntegerField(_("max. users"), null=True, blank=True)
    max_invoices_per_month = models.PositiveIntegerField(_("max. invoices per month"), null=True, blank=True)
    features = ArrayField(
        models.CharField(max_length=20, choices=PlanFeature.choices), default=list, blank=True, verbose_name=_("features"),
        help_text=_("What the plan includes beyond the bookkeeping every plan has."),
    )
    is_default = models.BooleanField(_("default plan"), default=False, help_text=_("New organizations start on a trial of it."))
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order"]
        verbose_name = _("subscription plan")
        verbose_name_plural = _("subscription plans")

    def __str__(self):
        return self.name


class Organization(TimeStampedModel, Address):
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
    phone = models.CharField(_("phone"), max_length=30, blank=True, validators=[phone_validator])
    email = models.EmailField(_("email"), blank=True)

    is_pkp = models.BooleanField(
        _("VAT-registered business (PKP)"),
        default=False,
        help_text=_("Enable if the business is registered as a PKP and must charge PPN (VAT)."),
    )
    is_retailer = models.BooleanField(
        _("retailer (PKP pedagang eceran)"), default=False,
        help_text=_("Sells to end consumers: sales to customers with neither an NPWP nor an identity document are reported as a monthly total (digunggung) in the SPT Masa PPN, without individual fakturs."),
    )
    uses_pph_final = models.BooleanField(
        _("uses MSME final income tax (PPh Final)"),
        default=True,
        # xgettext:no-python-format
        help_text=_("PP 55/2022: 0.5% final income tax on gross turnover."),
    )
    pph_final_since = models.PositiveSmallIntegerField(
        _("first year under PPh Final"), null=True, blank=True, validators=[MinValueValidator(EARLIEST_PPH_FINAL_YEAR)],
        help_text=_("The tax year the business registered for the MSME final income tax, or 2018 if earlier. PP 55/2022 allows "
                    "it for 7 tax years for an individual (sole proprietorship), 4 for a CV, Firma, cooperative or PT Perorangan "
                    "and 3 for a PT."),
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
        _("plan paid until"), null=True, blank=True, help_text=_("Last day covered by payment or by the trial; afterwards (and its grace days) the organization is read-only.")
    )
    renewal_reminder_sent_for = models.DateField(null=True, blank=True, editable=False)
    # The last day of the trial a new organization starts with (plan_paid_until until it pays).
    trial_ends_on = models.DateField(_("trial ends on"), null=True, blank=True, editable=False)
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

    @property
    def is_limited_company(self) -> bool:
        """A PT: its equity shows share capital and additional paid-in capital (SAK EMKM 13.8)."""
        return self.legal_form in (self.LegalForm.PT, self.LegalForm.PT_PERORANGAN)

    def subscription_state(self, today=None) -> SubscriptionState:
        return subscription_state(
            is_free_plan=self.plan.monthly_price <= 0, paid_until=self.plan_paid_until, trial_ends_on=self.trial_ends_on,
            today=today or timezone.localdate(), reminder_days=settings.BILLING_REMINDER_DAYS, grace_days=settings.BILLING_GRACE_DAYS,
        )

    @property
    def is_read_only(self) -> bool:
        """The trial, or the paid period and its grace days, has ended: nothing new is recorded until it pays."""
        return self.subscription_state().is_read_only

    def has_feature(self, feature: PlanFeature) -> bool:
        return feature in self.plan.features

    @property
    def pph_final_last_year(self) -> int | None:
        """The last tax year PP 55/2022 (Art. 59) allows the MSME final income tax, if the first is known."""
        return pph_final_last_year(self.legal_form, self.pph_final_since) if self.pph_final_since else None

    @property
    def is_partnership(self) -> bool:
        """A CV or Firma: its equity is the partners' capital (SAK EMKM Bab 13)."""
        return self.legal_form in (self.LegalForm.CV, self.LegalForm.FIRMA)

    @property
    def is_cooperative(self) -> bool:
        """A koperasi: its equity is the members' savings, reserves and retained surplus (SHU)."""
        return self.legal_form == self.LegalForm.KOPERASI


class Invitation(TimeStampedModel):
    """An invitation by email to join an organization, for someone without an account yet.

    The link carries a random token; only its keyed hash is stored. It is accepted once, by
    the user who signed up with the invited email address, before it expires."""

    VALID_FOR_DAYS = 7

    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="invitations")
    email = models.EmailField(_("email"))
    role = models.CharField(_("role"), max_length=20, choices=Role.choices)
    token_hash = models.CharField(max_length=64, unique=True, editable=False)
    invited_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    expires_at = models.DateTimeField(_("expires"))
    accepted_at = models.DateTimeField(_("accepted"), null=True, blank=True)
    accepted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    revoked_at = models.DateTimeField(_("revoked"), null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("invitation")
        verbose_name_plural = _("invitations")

    def __str__(self):
        return f"{self.email} → {self.organization}"

    @property
    def is_pending(self) -> bool:
        from django.utils import timezone

        return self.accepted_at is None and self.revoked_at is None and self.expires_at > timezone.now()


class BusinessPlace(TenantOwnedModel, Address):
    """A branch or other place of business (tempat kegiatan usaha) registered with DJP under
    its own NITKU. A sales invoice made from it carries that NITKU and address; invoices
    without one come from the head office (the NPWP followed by 000000)."""

    name = models.CharField(_("name"), max_length=100, help_text=_("E.g. Cabang Sleman."))
    nitku = models.CharField(
        _("NITKU"), max_length=22, validators=[nitku_validator],
        help_text=_("22 digits: the organization's 16-digit NPWP followed by the 6 digits of this place, as registered in Coretax."),
    )
    is_active = models.BooleanField(_("active"), default=True)

    class Meta:
        ordering = ["name"]
        verbose_name = _("place of business")
        verbose_name_plural = _("places of business")
        constraints = [models.UniqueConstraint(fields=["organization", "nitku"], name="unique_business_place_nitku")]

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        if self.nitku and self.organization_id:
            try:
                validate_nitku_of(self.nitku, self.organization.npwp)
            except ValidationError as exc:
                raise ValidationError({"nitku": exc}) from exc


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
