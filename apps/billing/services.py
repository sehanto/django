"""Subscription billing use cases.

Flow: ``start_checkout`` creates a pending ``SubscriptionInvoice`` and a hosted
payment link. The gateway's webhook (or the customer pressing "Check payment
status") calls ``sync_payment``, which asks the gateway for the authoritative
status and, once paid, extends the organization's plan. Activation is
idempotent and only happens when the paid amount matches the invoice.
"""

import logging
import re
import uuid
from collections.abc import Callable
from datetime import date, datetime, timedelta
from decimal import Decimal
from urllib.parse import urlsplit

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import formats, timezone, translation
from django.utils.translation import gettext as _

from apps.core.exceptions import DomainError, ValidationFailed
from apps.tenants.models import Membership, Organization, Plan
from apps.tenants.roles import Role
from apps.tenants.services import ensure_usage_fits, get_default_plan, set_subscription

from .domain import BillingPeriod, compute_charge, coverage_for_payment
from .gateways import get_gateway
from .gateways.base import CheckoutRequest, GatewayError, PaymentStatus
from .models import SubscriptionInvoice

logger = logging.getLogger(__name__)
Status = SubscriptionInvoice.Status


class WebhookRejected(DomainError):
    """The webhook could not be authenticated."""


class PaymentAmountMismatch(DomainError):
    """The gateway reports a paid amount different from the invoice total."""


def price_for(plan: Plan, period: BillingPeriod) -> Decimal:
    return plan.yearly_price if period == BillingPeriod.YEARLY else plan.monthly_price


# --- Checkout -------------------------------------------------------------------------------


def _reusable_checkout(organization, plan, period) -> SubscriptionInvoice | None:
    pending = SubscriptionInvoice.objects.filter(
        organization=organization, plan=plan, billing_period=period, status=Status.PENDING,
        expires_at__gt=timezone.now() + timedelta(minutes=10),
    ).exclude(payment_url="")
    return pending.order_by("-created_at").first()


def start_checkout(
    *, organization: Organization, plan: Plan, period: str, created_by=None,
    return_url_for: Callable[[SubscriptionInvoice], str] = lambda invoice: "",
) -> SubscriptionInvoice:
    """Create (or reuse) a pending subscription invoice with a hosted payment link."""
    period = BillingPeriod(period)
    price = price_for(plan, period)
    if price <= 0:
        raise ValidationFailed(_("This plan is free and needs no payment."))
    ensure_usage_fits(organization=organization, plan=plan)
    if existing := _reusable_checkout(organization, plan, period):
        return existing

    charge = compute_charge(price)
    gateway = get_gateway()
    with transaction.atomic():
        invoice = SubscriptionInvoice.objects.create(
            number=f"tmp-{uuid.uuid4().hex[:20]}",
            organization=organization, plan=plan, billing_period=period,
            buyer_name=organization.name, buyer_npwp=organization.npwp,
            buyer_address=", ".join(filter(None, [organization.address, organization.city])),
            buyer_email=organization.email or getattr(created_by, "email", ""),
            subtotal=charge.subtotal, tax_base=charge.tax_base, tax_amount=charge.tax, total=charge.total,
            gateway=gateway.name, created_by=created_by,
        )
        invoice.number = f"SUB-{timezone.localdate():%Y%m}-{invoice.pk:06d}"
        invoice.save(update_fields=["number"])

    expires_at = timezone.now() + timedelta(hours=settings.BILLING_CHECKOUT_HOURS)
    try:
        checkout = gateway.create_checkout(CheckoutRequest(
            reference=invoice.number,
            amount=invoice.total,
            description=_("%(seller)s %(plan)s plan (%(period)s), incl. PPN") % {
                "seller": settings.BILLING_SELLER_NAME, "plan": plan.name, "period": period.label,
            },
            expires_at=expires_at,
            locale=translation.get_language() or settings.LANGUAGE_CODE,
            return_url=return_url_for(invoice),
            metadata={"invoice": invoice.number, "organization_id": str(organization.pk)},
        ))
    except GatewayError:
        invoice.status = Status.FAILED
        invoice.save(update_fields=["status", "updated_at"])
        raise
    if not _is_safe_payment_url(checkout.payment_url) or not REFERENCE_PATTERN.match(checkout.gateway_reference):
        logger.error("Gateway returned an unexpected checkout for %s: %r", invoice.number, checkout)
        invoice.status = Status.FAILED
        invoice.save(update_fields=["status", "updated_at"])
        raise GatewayError(_("The payment provider did not return a payment link."))
    invoice.gateway_reference = checkout.gateway_reference
    invoice.payment_url = checkout.payment_url
    invoice.expires_at = checkout.expires_at or expires_at
    invoice.save(update_fields=["gateway_reference", "payment_url", "expires_at", "updated_at"])
    return invoice


def _is_safe_payment_url(url: str) -> bool:
    """Customers are redirected here, so only HTTPS URLs (or a same-site path, for the fake gateway)."""
    parts = urlsplit(url or "")
    if parts.scheme == "https" and parts.netloc:
        return True
    return not parts.scheme and not parts.netloc and parts.path.startswith("/") and not url.startswith("//")


# --- Payment confirmation ---------------------------------------------------------------


@transaction.atomic
def _record_payment(invoice_pk: int, *, amount: Decimal, payment_id: str, paid_at: datetime | None = None) -> SubscriptionInvoice:
    invoice = SubscriptionInvoice.objects.select_for_update().select_related("plan").get(pk=invoice_pk)
    if invoice.status == Status.PAID:
        return invoice  # webhooks are retried; activation must happen exactly once
    if amount != invoice.total:
        logger.error("Payment amount mismatch on %s: paid %s, expected %s", invoice.number, amount, invoice.total)
        raise PaymentAmountMismatch(_("The paid amount does not match the invoice. Our team will review it."))

    paid_at = paid_at or timezone.now()
    if invoice.organization_id:
        organization = Organization.objects.select_for_update().get(pk=invoice.organization_id)
        start, end = coverage_for_payment(
            paid_on=timezone.localdate(paid_at),
            period=BillingPeriod(invoice.billing_period),
            renewing_same_plan=organization.plan_id == invoice.plan_id,
            current_paid_until=organization.plan_paid_until,
        )
        set_subscription(organization=organization, plan=invoice.plan, paid_until=end)
        invoice.coverage_start, invoice.coverage_end = start, end
    invoice.status = Status.PAID
    invoice.paid_at = paid_at
    invoice.gateway_payment_id = payment_id
    invoice.save()
    logger.info("Subscription invoice %s paid; plan active until %s", invoice.number, invoice.coverage_end)
    return invoice


def sync_payment(invoice: SubscriptionInvoice) -> SubscriptionInvoice:
    """Ask the gateway for the authoritative status of a pending invoice and apply it."""
    if invoice.status != Status.PENDING or not invoice.gateway_reference:
        return invoice
    gateway = get_gateway()
    if gateway.name != invoice.gateway:
        return invoice  # created under another gateway configuration
    payment = gateway.fetch_payment(invoice.gateway_reference)
    if payment.status == PaymentStatus.PAID:
        return _record_payment(invoice.pk, amount=payment.amount, payment_id=payment.payment_id)
    if payment.status in (PaymentStatus.EXPIRED, PaymentStatus.CANCELED):
        closed = Status.EXPIRED if payment.status == PaymentStatus.EXPIRED else Status.CANCELED
        SubscriptionInvoice.objects.filter(pk=invoice.pk, status=Status.PENDING).update(status=closed, updated_at=timezone.now())
    invoice.refresh_from_db()
    return invoice


# Gateway and invoice references we accept from outside (e.g. "ps-661f…", "SUB-202610-000001").
REFERENCE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,63}$")


def handle_webhook(*, headers, payload: dict) -> SubscriptionInvoice | None:
    """Authenticate a gateway notification, then re-check the payment with the gateway."""
    gateway = get_gateway()
    if not gateway.verify_webhook(headers):
        raise WebhookRejected("Invalid webhook token.")
    if not isinstance(payload, dict):
        raise ValidationFailed("Webhook payload must be a JSON object.")
    references = [ref for ref in gateway.webhook_references(payload) if isinstance(ref, str) and REFERENCE_PATTERN.match(ref)]
    invoice = (
        SubscriptionInvoice.objects.filter(gateway=gateway.name)
        .filter(Q(gateway_reference__in=references) | Q(number__in=references))
        .first()
        if references else None
    )
    if invoice is None:
        logger.info("Ignoring %s webhook for unknown references %s", gateway.name, references)
        return None
    return sync_payment(invoice)


def mark_paid_manually(*, invoice: SubscriptionInvoice, confirmed_by) -> SubscriptionInvoice:
    """Support path for a confirmed bank transfer outside the gateway."""
    if invoice.status == Status.PAID:
        raise ValidationFailed(_("This invoice is already paid."))
    return _record_payment(invoice.pk, amount=invoice.total, payment_id=f"manual:{confirmed_by.email}")


# --- Scheduled jobs (see ``manage.py process_billing``) -----------------------------------


def expire_stale_checkouts(*, now: datetime | None = None) -> int:
    """Close payment links that have expired, after a final check for a late payment."""
    now = now or timezone.now()
    closed = 0
    for invoice in SubscriptionInvoice.objects.filter(status=Status.PENDING, expires_at__lt=now):
        try:
            invoice = sync_payment(invoice)
        except (GatewayError, PaymentAmountMismatch):
            continue
        if invoice.status == Status.PENDING:
            invoice.status = Status.EXPIRED
            invoice.save(update_fields=["status", "updated_at"])
        closed += invoice.status != Status.PAID
    return closed


def lapse_unpaid_subscriptions(*, today: date | None = None) -> int:
    """Move organizations whose paid period plus grace has ended back to the free plan."""
    today = today or timezone.localdate()
    cutoff = today - timedelta(days=settings.BILLING_GRACE_DAYS)
    default_plan = get_default_plan()
    lapsed = Organization.objects.filter(plan_paid_until__lt=cutoff).exclude(plan=default_plan)
    count = 0
    for organization in lapsed:
        set_subscription(organization=organization, plan=default_plan, paid_until=None)
        logger.info("Organization %s lapsed to the %s plan", organization.pk, default_plan.code)
        count += 1
    return count


def _billing_contacts(organization):
    return [
        m.user for m in Membership.objects.filter(organization=organization, role__in=[Role.OWNER, Role.ADMIN]).select_related("user")
        if m.user.is_active
    ]


def send_renewal_reminders(*, today: date | None = None) -> int:
    """Email owners/admins once per paid period when it is about to end (each in their language)."""
    today = today or timezone.localdate()
    window_end = today + timedelta(days=settings.BILLING_REMINDER_DAYS)
    due = Organization.objects.filter(plan_paid_until__gte=today, plan_paid_until__lte=window_end).select_related("plan")
    billing_url = settings.SITE_URL.rstrip("/") + reverse("billing:overview")
    sent = 0
    for organization in due:
        if organization.renewal_reminder_sent_for == organization.plan_paid_until:
            continue
        for user in _billing_contacts(organization):
            with translation.override(user.language):
                context = {
                    "plan": organization.plan.name, "organization": organization.name,
                    "date": formats.date_format(organization.plan_paid_until, "d F Y"), "url": billing_url,
                }
                send_mail(
                    subject=_("Your %(plan)s plan for %(organization)s ends on %(date)s") % context,
                    message=_(
                        "Hello,\n\nThe %(plan)s plan for %(organization)s is paid until %(date)s. "
                        "Renew before then to keep your plan without interruption:\n%(url)s\n\n"
                        "If it is not renewed, the organization moves to the free plan after a short grace period."
                    ) % context,
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[user.email],
                )
                sent += 1
        organization.renewal_reminder_sent_for = organization.plan_paid_until
        organization.save(update_fields=["renewal_reminder_sent_for"])
    return sent
