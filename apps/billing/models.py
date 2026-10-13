from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel, money_field

from .domain import BillingPeriod
from .gateways.base import GatewayName


class SubscriptionInvoice(TimeStampedModel):
    """A bill from us (the SaaS seller) to a tenant for one prepaid plan period.

    These are the seller's own sales records: buyer details are snapshotted and
    the row survives if the tenant later deletes its organization.
    """

    class Status(models.TextChoices):
        PENDING = "pending", _("Awaiting payment")
        PAID = "paid", _("Paid")
        EXPIRED = "expired", _("Expired")
        CANCELED = "canceled", _("Canceled")
        FAILED = "failed", _("Failed")

    number = models.CharField(_("number"), max_length=30, unique=True)
    organization = models.ForeignKey(
        "tenants.Organization", null=True, on_delete=models.SET_NULL, related_name="subscription_invoices",
        verbose_name=_("organization"),
    )
    plan = models.ForeignKey("tenants.Plan", on_delete=models.PROTECT, related_name="+", verbose_name=_("plan"))
    billing_period = models.CharField(_("billing period"), max_length=10, choices=BillingPeriod.choices)

    buyer_name = models.CharField(_("buyer name"), max_length=200)
    buyer_npwp = models.CharField(_("buyer NPWP"), max_length=16, blank=True)
    buyer_address = models.TextField(_("buyer address"), blank=True)
    buyer_email = models.EmailField(_("buyer email"), blank=True)

    subtotal = money_field(_("subtotal"))
    # Proration: unused time on the previous plan, deducted before PPN.
    credit_amount = money_field(_("credit for unused time"))
    credited_plan = models.ForeignKey(
        "tenants.Plan", null=True, blank=True, on_delete=models.PROTECT, related_name="+", verbose_name=_("credited plan")
    )
    credited_days = models.PositiveIntegerField(_("credited days"), default=0)
    credit_basis_until = models.DateField(null=True, blank=True, editable=False)  # paid-until the credit was based on
    extension_days = models.PositiveIntegerField(_("extra days from credit"), default=0)
    coverage_value = money_field(_("value of coverage"))  # excl. PPN; for valuing unused days later
    tax_base = money_field(_("tax base (DPP)"))
    tax_amount = money_field(_("PPN"))
    total = money_field(_("total"))

    status = models.CharField(_("status"), max_length=10, choices=Status.choices, default=Status.PENDING)
    gateway = models.CharField(_("payment gateway"), max_length=20, choices=GatewayName.choices)
    gateway_reference = models.CharField(_("gateway reference"), max_length=64, blank=True)
    gateway_payment_id = models.CharField(_("gateway payment ID"), max_length=64, blank=True)
    payment_url = models.URLField(_("payment link"), max_length=500, blank=True)
    expires_at = models.DateTimeField(_("payment link expires"), null=True, blank=True)
    paid_at = models.DateTimeField(_("paid at"), null=True, blank=True)
    coverage_start = models.DateField(_("covered from"), null=True, blank=True)
    coverage_end = models.DateField(_("covered until"), null=True, blank=True)
    # Set when a switch to another plan ends this coverage early; its unused time
    # was credited (or forfeited) then and must never be credited again.
    superseded_on = models.DateField(_("ended early on"), null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("subscription invoice")
        verbose_name_plural = _("subscription invoices")
        constraints = [
            models.UniqueConstraint(
                fields=["gateway", "gateway_reference"], condition=~Q(gateway_reference=""), name="unique_gateway_reference"
            ),
        ]
        indexes = [models.Index(fields=["status", "expires_at"])]

    def __str__(self):
        return self.number

    @property
    def net_subtotal(self):
        return self.subtotal - self.credit_amount

    @property
    def is_payable(self) -> bool:
        return (
            self.status == self.Status.PENDING
            and bool(self.payment_url)
            and (self.expires_at is None or self.expires_at > timezone.now())
        )


class AutoRenewal(TimeStampedModel):
    """An organization's standing order to renew its plan with a saved payment method.

    Set up on the gateway's hosted page (nothing charged then); afterwards the nightly billing
    job charges the saved method shortly before the paid period ends. The token is stored
    encrypted (``apps.core.crypto``)."""

    class Status(models.TextChoices):
        SETUP = "setup", _("Waiting for the payment method")
        ACTIVE = "active", _("Active")
        CANCELED = "canceled", _("Turned off")

    organization = models.OneToOneField(
        "tenants.Organization", on_delete=models.CASCADE, related_name="auto_renewal", verbose_name=_("organization"),
    )
    billing_period = models.CharField(_("billing period"), max_length=10, choices=BillingPeriod.choices)
    status = models.CharField(_("status"), max_length=10, choices=Status.choices, default=Status.SETUP)
    gateway = models.CharField(_("payment gateway"), max_length=20, choices=GatewayName.choices)
    setup_reference = models.CharField(max_length=64, blank=True)
    setup_url = models.URLField(max_length=500, blank=True)
    payment_token = models.TextField(blank=True, editable=False)  # encrypted
    failed_attempts = models.PositiveSmallIntegerField(_("failed attempts"), default=0)
    last_error = models.CharField(_("last error"), max_length=255, blank=True)
    activated_at = models.DateTimeField(_("activated"), null=True, blank=True)
    # The paid-until date last charged for: one charge per period, however often the job runs.
    last_charged_for = models.DateField(null=True, blank=True, editable=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    MAX_FAILED_ATTEMPTS = 3

    class Meta:
        verbose_name = _("automatic renewal")
        verbose_name_plural = _("automatic renewals")

    def __str__(self):
        return f"{self.organization} ({self.get_status_display()})"

    @property
    def last_error_label(self) -> str:
        """The last failed charge's invoice status (``last_error`` holds its value)."""
        values = SubscriptionInvoice.Status.values
        return SubscriptionInvoice.Status(self.last_error).label if self.last_error in values else self.last_error
