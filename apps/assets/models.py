from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TenantOwnedModel, money_field
from apps.core.money import ZERO

from .domain import MAX_USEFUL_LIFE_MONTHS, AssetStatus, DepreciationMethod, Estimate, ScheduledMonth, depreciation_schedule


class FixedAsset(TenantOwnedModel):
    """One item in the fixed asset register: a sub-ledger of the fixed asset accounts.

    Registering an asset posts nothing (its purchase is already in the books, from a
    purchase bill or a journal entry); the register posts its depreciation every month
    and its disposal. Once depreciation is posted or the asset is disposed, its figures
    are fixed (assets migration 0002); only the name and description can change.
    """

    Status = AssetStatus
    Method = DepreciationMethod
    # What the depreciation schedule is computed from.
    FIXED_ONCE_DEPRECIATED = (
        "asset_account", "accumulated_depreciation_account", "depreciation_expense_account", "acquisition_date",
        "cost", "residual_value", "is_depreciable", "useful_life_months", "method", "depreciated_before_until",
    )

    number = models.CharField(_("number"), max_length=30, editable=False)
    name = models.CharField(_("name"), max_length=150)
    description = models.CharField(_("description"), max_length=255, blank=True)
    asset_account = models.ForeignKey(
        "ledger.Account", on_delete=models.PROTECT, related_name="+", verbose_name=_("fixed asset account"),
        help_text=_("Where the asset's cost is recorded, e.g. Peralatan Usaha."),
    )
    acquisition_date = models.DateField(_("acquisition date"))
    cost = money_field(_("cost"), validators=[MinValueValidator(1)], help_text=_("Purchase price plus the costs of getting the asset ready for use, excluding refundable PPN."))
    is_depreciable = models.BooleanField(_("depreciable"), default=True, help_text=_("Turn off for land, which is not depreciated."))
    residual_value = money_field(_("residual value"), validators=[MinValueValidator(0)], help_text=_("What the asset is expected to be worth at the end of its useful life. Usually 0."))
    useful_life_months = models.PositiveSmallIntegerField(
        _("useful life (months)"), null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(MAX_USEFUL_LIFE_MONTHS)],
        help_text=_("E.g. 48 for office equipment, 96 for vehicles (tax group 1 and 2)."),
    )
    method = models.CharField(_("depreciation method"), max_length=20, choices=DepreciationMethod.choices, editable=False)
    accumulated_depreciation_account = models.ForeignKey(
        "ledger.Account", on_delete=models.PROTECT, related_name="+", null=True, blank=True,
        verbose_name=_("accumulated depreciation account"),
    )
    depreciation_expense_account = models.ForeignKey(
        "ledger.Account", on_delete=models.PROTECT, related_name="+", null=True, blank=True,
        verbose_name=_("depreciation expense account"),
    )
    depreciated_before_until = models.DateField(
        _("already depreciated through"), null=True, blank=True,
        help_text=_("Only for assets brought over from earlier books: depreciation up to this month is already in your opening balances and is not posted again."),
    )
    status = models.CharField(_("status"), max_length=10, choices=AssetStatus.choices, default=AssetStatus.ACTIVE)
    disposed_on = models.DateField(_("disposal date"), null=True, blank=True)
    disposal_proceeds = money_field(_("sale proceeds"))
    disposal_entry = models.OneToOneField("ledger.JournalEntry", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    # A sale on a sales invoice (Faktur Pajak code 09 for a PKP): the proceeds are invoiced
    # instead of received in cash, so the invoice credits them to the asset account.
    disposal_invoice = models.ForeignKey(
        "invoicing.Invoice", on_delete=models.PROTECT, null=True, blank=True, related_name="disposed_assets",
        verbose_name=_("sales invoice"),
    )
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["acquisition_date", "number"]
        verbose_name = _("fixed asset")
        verbose_name_plural = _("fixed assets")
        constraints = [
            models.UniqueConstraint(fields=["organization", "number"], name="unique_fixed_asset_number"),
            models.CheckConstraint(
                name="depreciable_assets_have_a_life_and_accounts",
                condition=models.Q(is_depreciable=False) | models.Q(
                    useful_life_months__isnull=False, accumulated_depreciation_account__isnull=False,
                    depreciation_expense_account__isnull=False,
                ),
            ),
            models.CheckConstraint(name="residual_below_cost", condition=models.Q(residual_value__lt=models.F("cost"))),
        ]

    def __str__(self):
        return f"{self.number} · {self.name}"

    @property
    def estimates(self) -> list["AssetRevision"]:
        return sorted(self.revisions.all(), key=lambda r: (r.effective_from, r.pk))

    @property
    def current_useful_life_months(self) -> int | None:
        revisions = self.estimates
        return revisions[-1].useful_life_months if revisions else self.useful_life_months

    @property
    def current_method(self) -> str:
        revisions = self.estimates
        return revisions[-1].method if revisions else self.method

    def get_current_method_display(self) -> str:
        return DepreciationMethod(self.current_method).label

    @property
    def current_residual_value(self):
        revisions = self.estimates
        return revisions[-1].residual_value if revisions else self.residual_value

    @property
    def schedule(self) -> list[ScheduledMonth]:
        if not self.is_depreciable:
            return []
        return depreciation_schedule(
            cost=self.cost, residual_value=self.residual_value, useful_life_months=self.useful_life_months,
            method=self.method, acquired_on=self.acquisition_date,
            revisions=[r.estimate for r in self.estimates],
        )

    def is_booked_elsewhere(self, month: ScheduledMonth) -> bool:
        """Months already covered by the opening balances."""
        return self.depreciated_before_until is not None and month.period_end <= self.depreciated_before_until

    @property
    def opening_depreciation(self):
        """Accumulated depreciation already in the opening balances."""
        booked = [m for m in self.schedule if self.is_booked_elsewhere(m)]
        return booked[-1].accumulated if booked else ZERO


class AssetRevision(TenantOwnedModel):
    """A change in accounting estimate: a new useful life, residual value and depreciation
    method from a month on.

    Applied prospectively (past depreciation stays as posted). Append-only in the database
    (assets migration 0004): it starts after the last posted month and after earlier
    revisions; a mistake is corrected by another revision.
    """

    asset = models.ForeignKey(FixedAsset, on_delete=models.PROTECT, related_name="revisions", verbose_name=_("fixed asset"))
    effective_from = models.DateField(_("applies from"), help_text=_("The first month the new estimate applies to."))
    useful_life_months = models.PositiveSmallIntegerField(
        _("useful life (months)"), validators=[MinValueValidator(1), MaxValueValidator(MAX_USEFUL_LIFE_MONTHS)],
        help_text=_("The whole useful life, counted from the month of acquisition."),
    )
    residual_value = money_field(_("residual value"), validators=[MinValueValidator(0)])
    previous_useful_life_months = models.PositiveSmallIntegerField(_("previous useful life (months)"))
    previous_residual_value = money_field(_("previous residual value"))
    method = models.CharField(_("depreciation method"), max_length=20, choices=DepreciationMethod.choices)
    previous_method = models.CharField(_("previous depreciation method"), max_length=20, choices=DepreciationMethod.choices)
    reason = models.CharField(_("reason"), max_length=255)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["effective_from", "pk"]
        verbose_name = _("change in estimate")
        verbose_name_plural = _("changes in estimates")

    def __str__(self):
        return f"{self.asset.number} {self.effective_from:%m/%Y}"

    @property
    def estimate(self) -> Estimate:
        return Estimate((self.effective_from.year, self.effective_from.month), self.useful_life_months, self.residual_value, self.method)


class DepreciationPosting(TenantOwnedModel):
    """One asset's depreciation for one month, as posted in that month's journal entry.

    Append-only in the database (assets migration 0002): never changed or deleted.
    """

    asset = models.ForeignKey(FixedAsset, on_delete=models.PROTECT, related_name="postings", verbose_name=_("fixed asset"))
    year = models.PositiveSmallIntegerField(_("year"))
    month = models.PositiveSmallIntegerField(_("month"), validators=[MinValueValidator(1), MaxValueValidator(12)])
    amount = money_field(_("amount"), validators=[MinValueValidator(0)])
    journal_entry = models.ForeignKey("ledger.JournalEntry", on_delete=models.PROTECT, related_name="+")

    class Meta:
        ordering = ["year", "month"]
        verbose_name = _("depreciation posting")
        verbose_name_plural = _("depreciation postings")
        constraints = [models.UniqueConstraint(fields=["asset", "year", "month"], name="unique_depreciation_month")]

    def __str__(self):
        return f"{self.asset.number} {self.month:02d}/{self.year}"
