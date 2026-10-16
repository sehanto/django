from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from apps.core.models import TenantOwnedModel, money_field

from .domain import StatementLineStatus


class StatementLine(TenantOwnedModel):
    """One line of a bank statement as the bank reported it (uploaded, never edited; banking 0002):
    money in is positive, money out negative. Reconciling links it to the posted journal line
    that recorded the same movement on the bank account, once."""

    Status = StatementLineStatus

    account = models.ForeignKey("ledger.Account", on_delete=models.PROTECT, related_name="+", verbose_name=_("bank account"))
    date = models.DateField(_("date"))
    description = models.CharField(_("description"), max_length=255)
    reference = models.CharField(_("reference"), max_length=100, blank=True)
    amount = money_field(_("amount"))
    balance = models.DecimalField(_("balance"), max_digits=18, decimal_places=2, null=True, blank=True)
    key = models.CharField(_("line key"), max_length=64, editable=False)
    status = models.CharField(_("status"), max_length=10, choices=StatementLineStatus.choices, default=StatementLineStatus.UNMATCHED)
    journal_line = models.OneToOneField(
        "ledger.JournalLine", null=True, blank=True, on_delete=models.PROTECT, related_name="statement_line",
        verbose_name=_("journal line"),
    )
    reconciled_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    reconciled_at = models.DateTimeField(_("reconciled at"), null=True, blank=True)

    class Meta:
        ordering = ["date", "id"]
        verbose_name = _("bank statement line")
        verbose_name_plural = _("bank statement lines")
        constraints = [
            models.UniqueConstraint(fields=["organization", "key"], name="statement_line_once"),
            models.CheckConstraint(name="statement_line_has_amount", condition=~Q(amount=0)),
            models.CheckConstraint(
                name="statement_line_matched_with_journal_line",
                condition=Q(status=StatementLineStatus.MATCHED, journal_line__isnull=False)
                | ~Q(status=StatementLineStatus.MATCHED) & Q(journal_line__isnull=True),
            ),
        ]
        indexes = [models.Index(fields=["organization", "account", "status", "date"])]

    def __str__(self):
        return f"{self.date:%d-%m-%Y} {self.description} {self.amount}"

    @property
    def is_deposit(self) -> bool:
        return self.amount > 0
