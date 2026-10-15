from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TenantOwnedModel, money_field

from .domain import MAX_TERM_MONTHS, DeferralKind, DeferralStatus, ScheduledMonth, straight_line


class Deferral(TenantOwnedModel):
    """An amount paid or received in advance (e.g. a year's rent), recognised month by month.

    The amount sits on ``balance_account`` (Biaya Dibayar di Muka, or Pendapatan Diterima di
    Muka) and moves to ``recognition_account`` (Beban Sewa, Pendapatan Sewa …) over the term,
    one journal entry per month. When the bill or invoice was recorded straight to the expense or
    revenue account, registering first moves the amount to the balance account (``opening_entry``,
    dated ``recorded_on``). Fixed once registered (deferrals migration 0002): a mistake is voided
    (the opening entry reversed) while nothing has been recognised yet.
    """

    Kind = DeferralKind
    Status = DeferralStatus

    number = models.CharField(_("number"), max_length=30, editable=False)
    kind = models.CharField(_("type"), max_length=20, choices=DeferralKind.choices)
    description = models.CharField(_("description"), max_length=255, help_text=_("E.g. shop rent January–December 2027."))
    amount = money_field(_("amount"), validators=[MinValueValidator(1)], help_text=_("Excluding PPN."))
    recorded_on = models.DateField(_("recorded on"), help_text=_("The date of the bill, invoice or payment that recorded the amount."))
    starts_on = models.DateField(_("first month"), help_text=_("The first month the amount covers."))
    months = models.PositiveSmallIntegerField(
        _("term (months)"), validators=[MinValueValidator(1), MaxValueValidator(MAX_TERM_MONTHS)],
    )
    balance_account = models.ForeignKey(
        "ledger.Account", on_delete=models.PROTECT, related_name="+", verbose_name=_("prepaid or received in advance account"),
    )
    recognition_account = models.ForeignKey(
        "ledger.Account", on_delete=models.PROTECT, related_name="+", verbose_name=_("expense or revenue account"),
    )
    opening_entry = models.OneToOneField("ledger.JournalEntry", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    status = models.CharField(_("status"), max_length=10, choices=DeferralStatus.choices, default=DeferralStatus.ACTIVE)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-starts_on", "-number"]
        verbose_name = _("prepayment")
        verbose_name_plural = _("prepayments")
        constraints = [
            models.UniqueConstraint(fields=["organization", "number"], name="unique_deferral_number"),
            models.CheckConstraint(name="deferral_amount_positive", condition=models.Q(amount__gt=0)),
            models.CheckConstraint(name="deferral_starts_on_first_day", condition=models.Q(starts_on__day=1)),
            models.CheckConstraint(name="deferral_term", condition=models.Q(months__gte=1, months__lte=MAX_TERM_MONTHS)),
        ]

    def __str__(self):
        return f"{self.number} · {self.description}"

    @property
    def is_prepaid_expense(self) -> bool:
        return self.kind == DeferralKind.PREPAID_EXPENSE

    @property
    def schedule(self) -> list[ScheduledMonth]:
        return straight_line(self.amount, self.months, (self.starts_on.year, self.starts_on.month))

    @property
    def ends_on(self):
        return self.schedule[-1].period_end


class DeferralPosting(TenantOwnedModel):
    """One deferral's amount recognised for one month, in that month's journal entry.

    Append-only in the database (deferrals migration 0002): never changed or deleted.
    """

    deferral = models.ForeignKey(Deferral, on_delete=models.PROTECT, related_name="postings", verbose_name=_("prepayment"))
    year = models.PositiveSmallIntegerField(_("year"))
    month = models.PositiveSmallIntegerField(_("month"), validators=[MinValueValidator(1), MaxValueValidator(12)])
    amount = money_field(_("amount"), validators=[MinValueValidator(0)])
    journal_entry = models.ForeignKey("ledger.JournalEntry", on_delete=models.PROTECT, related_name="+")

    class Meta:
        ordering = ["year", "month"]
        verbose_name = _("prepayment recognised")
        verbose_name_plural = _("prepayments recognised")
        constraints = [models.UniqueConstraint(fields=["deferral", "year", "month"], name="unique_deferral_month")]

    def __str__(self):
        return f"{self.deferral.number} {self.month:02d}/{self.year}"
