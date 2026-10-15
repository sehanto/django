"""Subscription rules of an organization (pure): what its plan includes and whether it may record.

Plans are tiers (Starter → Growth → Pro): each has limits (users, invoices a month) and features.
A new organization starts on a trial of the default plan; when the trial, or a paid period plus
its grace days, has ended, the organization is read-only until it pays: everything stays
readable and exportable, nothing new is recorded.
"""

from datetime import date, timedelta

from django.db import models
from django.utils.translation import gettext_lazy as _


class PlanFeature(models.TextChoices):
    """What a plan includes beyond the bookkeeping every plan has (sales, purchases, ledger, PPN,
    PPh, inventory, prepayments, reports, books export, Faktur and eBupot files)."""

    PAYROLL = "payroll", _("Payroll and PPh 21")
    API = "api", _("REST API")
    CORETAX_SENDING = "coretax_sending", _("Sending to Coretax")
    FIXED_ASSETS = "fixed_assets", _("Fixed asset register")


class SubscriptionState(models.TextChoices):
    TRIAL = "trial", _("Trial")
    ACTIVE = "active", _("Active")
    EXPIRING = "expiring", _("Expiring soon")
    GRACE = "grace", _("Expired (grace period)")
    LAPSED = "lapsed", _("Read-only")
    FREE = "free", _("Free plan")  # a plan without a price, e.g. one the operator gives away

    @property
    def is_read_only(self) -> bool:
        return self == SubscriptionState.LAPSED


def subscription_state(
    *, is_free_plan: bool, paid_until: date | None, trial_ends_on: date | None, today: date, reminder_days: int, grace_days: int,
) -> SubscriptionState:
    """``paid_until`` is the last day covered, by payment or by the trial (``trial_ends_on``, no
    grace days after it: the trial simply ends)."""
    if is_free_plan:
        return SubscriptionState.FREE
    if paid_until is None:
        return SubscriptionState.LAPSED
    if paid_until == trial_ends_on:  # still the trial's own days: nothing paid yet
        return SubscriptionState.TRIAL if today <= paid_until else SubscriptionState.LAPSED
    if today <= paid_until - timedelta(days=reminder_days):
        return SubscriptionState.ACTIVE
    if today <= paid_until:
        return SubscriptionState.EXPIRING
    if today <= paid_until + timedelta(days=grace_days):
        return SubscriptionState.GRACE
    return SubscriptionState.LAPSED
