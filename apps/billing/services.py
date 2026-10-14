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

from .domain import BillingPeriod, Credit, PaidPeriod, SwitchQuote, coverage_for_payment, switch_quote, unused_credit
from .gateways import get_gateway
from apps.core.crypto import decrypt, encrypt

from .gateways.base import ChargeRequest, CheckoutRequest, GatewayError, GatewayName, PaymentStatus, SaveRequest
from .models import AutoRenewal, SubscriptionInvoice

logger = logging.getLogger(__name__)
Status = SubscriptionInvoice.Status


class WebhookRejected(DomainError):
    """The webhook could not be authenticated."""


class PaymentAmountMismatch(DomainError):
    """The gateway reports a paid amount different from the invoice total."""


def price_for(plan: Plan, period: BillingPeriod) -> Decimal:
    return plan.yearly_price if period == BillingPeriod.YEARLY else plan.monthly_price


# --- Proration ------------------------------------------------------------------------------


def _paid_periods(organization, plan) -> list[PaidPeriod]:
    """Paid, not yet superseded coverage of ``plan`` (renewals can stack several)."""
    invoices = SubscriptionInvoice.objects.filter(
        organization=organization, plan=plan, status=Status.PAID, superseded_on__isnull=True,
        coverage_start__isnull=False, coverage_end__isnull=False,
    )
    return [PaidPeriod(inv.coverage_start, inv.coverage_end, inv.coverage_value) for inv in invoices]


def switch_credit(organization, plan: Plan, *, today: date | None = None) -> Credit:
    """Credit for unused paid time when moving to a *different* plan (none when renewing)."""
    today = today or timezone.localdate()
    current = organization.plan
    if plan.pk == current.pk or not organization.plan_paid_until or organization.plan_paid_until <= today:
        return Credit.none()
    return unused_credit(_paid_periods(organization, current), today)


def quote_for(organization, plan: Plan, period: BillingPeriod, *, today: date | None = None) -> SwitchQuote:
    today = today or timezone.localdate()
    return switch_quote(
        price=price_for(plan, period), credit=switch_credit(organization, plan, today=today), period=period, starts_on=today
    )


def _supersede_other_plans(organization, new_plan, today: date) -> None:
    """A switch ends the previous plan's paid time now: it was credited (or forfeited) once."""
    SubscriptionInvoice.objects.filter(
        organization=organization, status=Status.PAID, superseded_on__isnull=True, coverage_end__gte=today,
    ).exclude(plan=new_plan).update(superseded_on=today)


# --- Checkout -------------------------------------------------------------------------------


def _reusable_checkout(organization, plan, period, credit: Decimal) -> SubscriptionInvoice | None:
    pending = SubscriptionInvoice.objects.filter(
        organization=organization, plan=plan, billing_period=period, status=Status.PENDING,
        expires_at__gt=timezone.now() + timedelta(minutes=10),
        credit_amount=credit,  # the credit shrinks daily; an older quote isn't reused
    ).exclude(payment_url="")
    return pending.order_by("-created_at").first()


def _new_invoice(organization, plan, period, quote: SwitchQuote, credit: Credit, *, gateway: GatewayName, created_by, **extra):
    charge = quote.charge
    invoice = SubscriptionInvoice.objects.create(
        number=f"tmp-{uuid.uuid4().hex[:20]}",
        organization=organization, plan=plan, billing_period=period,
        buyer_name=organization.name, buyer_npwp=organization.npwp,
        buyer_address=organization.full_address, buyer_village_id=organization.village_id,
        buyer_email=organization.email or getattr(created_by, "email", ""),
        subtotal=charge.subtotal, credit_amount=charge.credit, tax_base=charge.tax_base, tax_amount=charge.tax,
        total=charge.total, coverage_value=extra.pop("coverage_value", charge.subtotal),
        credited_plan=organization.plan if charge.credit else None,
        credited_days=credit.days if charge.credit else 0,
        credit_basis_until=organization.plan_paid_until if charge.credit else None,
        extension_days=quote.extension_days,
        gateway=gateway, created_by=created_by, **extra,
    )
    invoice.number = f"SUB-{timezone.localdate():%Y%m}-{invoice.pk:06d}"
    invoice.save(update_fields=["number"])
    return invoice


@transaction.atomic
def _switch_with_credit(organization, plan, period, quote: SwitchQuote, credit: Credit, created_by) -> SubscriptionInvoice:
    """The credit covers the whole price: switch now, nothing to pay, leftover becomes extra days."""
    organization = Organization.objects.select_for_update().get(pk=organization.pk)
    today = timezone.localdate()
    start, end = coverage_for_payment(
        paid_on=today, period=period, renewing_same_plan=False, current_paid_until=None,
        extension_days=quote.extension_days,
    )
    invoice = _new_invoice(
        organization, plan, period, quote, credit, gateway=GatewayName.CREDIT, created_by=created_by,
        status=Status.PAID, paid_at=timezone.now(), coverage_start=start, coverage_end=end,
        coverage_value=credit.amount,  # all of the credit went into this coverage
    )
    _supersede_other_plans(organization, plan, today)
    set_subscription(organization=organization, plan=plan, paid_until=end)
    logger.info("Organization %s switched to %s using credit; covered until %s", organization.pk, plan.code, end)
    return invoice


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
    today = timezone.localdate()
    credit = switch_credit(organization, plan, today=today)
    quote = switch_quote(price=price, credit=credit, period=period, starts_on=today)
    if not quote.needs_payment:
        return _switch_with_credit(organization, plan, period, quote, credit, created_by)
    if existing := _reusable_checkout(organization, plan, period, quote.charge.credit):
        return existing

    gateway = get_gateway()
    with transaction.atomic():
        invoice = _new_invoice(organization, plan, period, quote, credit, gateway=gateway.name, created_by=created_by)

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
        paid_on = timezone.localdate(paid_at)
        share = Decimal(1)
        if invoice.credit_amount:
            # The credit was quoted at checkout. If the plan changed since (e.g. another
            # checkout was paid first), that time was already used: don't credit it twice.
            # The money paid then buys a proportional share of the period instead.
            credit_still_valid = (
                organization.plan_id == invoice.credited_plan_id
                and organization.plan_paid_until == invoice.credit_basis_until
            )
            if not credit_still_valid:
                share = invoice.net_subtotal / invoice.subtotal
                logger.warning("Credit on %s no longer valid at payment; covering %s of the period", invoice.number, share)
        start, end = coverage_for_payment(
            paid_on=paid_on,
            period=BillingPeriod(invoice.billing_period),
            renewing_same_plan=organization.plan_id == invoice.plan_id,
            current_paid_until=organization.plan_paid_until,
            extension_days=invoice.extension_days if share == 1 else 0,
            share=share,
        )
        invoice.coverage_value = invoice.subtotal if share == 1 else invoice.net_subtotal
        if organization.plan_id != invoice.plan_id:
            _supersede_other_plans(organization, invoice.plan, paid_on)
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
    if payment.status in (PaymentStatus.EXPIRED, PaymentStatus.CANCELED, PaymentStatus.FAILED):
        closed = {PaymentStatus.EXPIRED: Status.EXPIRED, PaymentStatus.CANCELED: Status.CANCELED}.get(payment.status, Status.FAILED)
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
        renewal = (
            AutoRenewal.objects.filter(gateway=gateway.name, status=AutoRenewal.Status.SETUP, setup_reference__in=references).first()
            if references else None
        )
        if renewal is not None:  # the customer saved a payment method
            sync_auto_renewal(renewal)
            return None
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
    """Email owners/admins once per paid period when it is about to end (each in their language).
    Organizations that renew automatically are charged instead."""
    today = today or timezone.localdate()
    window_end = today + timedelta(days=settings.BILLING_REMINDER_DAYS)
    due = (
        Organization.objects.filter(plan_paid_until__gte=today, plan_paid_until__lte=window_end)
        .exclude(auto_renewal__status=AutoRenewal.Status.ACTIVE).select_related("plan")
    )
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


# --- Automatic renewal ----------------------------------------------------------------------


def start_auto_renewal(*, organization: Organization, period: str, created_by, return_url: str = "") -> AutoRenewal:
    """Send the customer to the gateway's page to save a payment method for renewals."""
    period = BillingPeriod(period)
    if price_for(organization.plan, period) <= 0:
        raise ValidationFailed(_("The free plan needs no renewal. Choose a paid plan first."))
    gateway = get_gateway()
    checkout = gateway.create_save_session(SaveRequest(
        reference=f"AR-{organization.pk}-{uuid.uuid4().hex[:12]}", customer_reference=f"org-{organization.pk}",
        customer_name=getattr(created_by, "full_name", "") or organization.name,
        customer_email=organization.email or getattr(created_by, "email", ""),
        description=_("Automatic renewal of the %(seller)s %(plan)s plan") % {"seller": settings.BILLING_SELLER_NAME, "plan": organization.plan.name},
        expires_at=timezone.now() + timedelta(hours=settings.BILLING_CHECKOUT_HOURS),
        locale=translation.get_language() or settings.LANGUAGE_CODE, return_url=return_url,
    ))
    if not _is_safe_payment_url(checkout.payment_url) or not REFERENCE_PATTERN.match(checkout.gateway_reference):
        raise GatewayError(_("The payment provider did not return a payment link."))
    renewal, _created = AutoRenewal.objects.update_or_create(organization=organization, defaults={
        "billing_period": period, "status": AutoRenewal.Status.SETUP, "gateway": gateway.name,
        "setup_reference": checkout.gateway_reference, "setup_url": checkout.payment_url, "payment_token": "",
        "failed_attempts": 0, "last_error": "", "activated_at": None, "created_by": created_by,
    })
    return renewal


def sync_auto_renewal(renewal: AutoRenewal) -> AutoRenewal:
    """Activate the renewal once the gateway confirms the saved payment method."""
    if renewal.status != AutoRenewal.Status.SETUP or not renewal.setup_reference:
        return renewal
    gateway = get_gateway()
    if gateway.name != renewal.gateway:
        return renewal
    token = gateway.fetch_saved_token(renewal.setup_reference)
    if token:
        renewal.payment_token = encrypt(token.encode())
        renewal.status, renewal.activated_at = AutoRenewal.Status.ACTIVE, timezone.now()
        renewal.save(update_fields=["payment_token", "status", "activated_at", "updated_at"])
    return renewal


def cancel_auto_renewal(*, organization: Organization) -> AutoRenewal | None:
    renewal = AutoRenewal.objects.filter(organization=organization).first()
    if renewal is not None and renewal.status != AutoRenewal.Status.CANCELED:
        renewal.status, renewal.payment_token = AutoRenewal.Status.CANCELED, ""
        renewal.save(update_fields=["status", "payment_token", "updated_at"])
    return renewal


def _notify_renewal_failed(organization: Organization, error: str, stopped: bool) -> None:
    billing_url = settings.SITE_URL.rstrip("/") + reverse("billing:overview")
    for user in _billing_contacts(organization):
        with translation.override(user.language):
            context = {"plan": organization.plan.name, "organization": organization.name, "url": billing_url, "error": Status(error).label}
            message = _(
                "Hello,\n\nThe automatic renewal of the %(plan)s plan for %(organization)s could not be charged (%(error)s). "
                "Please renew it here to keep your plan:\n%(url)s"
            ) % context
            if stopped:
                message += "\n\n" + _("Automatic renewal has been turned off after repeated failures; you can turn it on again with another payment method.")
            send_mail(
                subject=_("Automatic renewal of %(organization)s failed") % context, message=message,
                from_email=settings.DEFAULT_FROM_EMAIL, recipient_list=[user.email],
            )


def charge_auto_renewals(*, today: date | None = None) -> tuple[int, int]:
    """Charge active automatic renewals whose paid period ends within
    ``BILLING_AUTO_RENEW_DAYS_BEFORE`` days, once per period. Returns (charged, failed)."""
    today = today or timezone.localdate()
    due_by = today + timedelta(days=settings.BILLING_AUTO_RENEW_DAYS_BEFORE)
    gateway = get_gateway()
    charged = failed = 0
    renewals = AutoRenewal.objects.filter(
        status=AutoRenewal.Status.ACTIVE, gateway=gateway.name,
        organization__plan_paid_until__lte=due_by,
        organization__plan_paid_until__gte=today - timedelta(days=settings.BILLING_GRACE_DAYS),
    ).select_related("organization__plan")
    for renewal in renewals:
        organization = renewal.organization
        plan, period, paid_until = organization.plan, BillingPeriod(renewal.billing_period), organization.plan_paid_until
        if renewal.last_charged_for == paid_until or price_for(plan, period) <= 0:
            continue
        with transaction.atomic():
            invoice = _new_invoice(
                organization, plan, period, quote_for(organization, plan, period, today=today),
                switch_credit(organization, plan, today=today), gateway=gateway.name, created_by=None,
                expires_at=timezone.now() + timedelta(days=3),
            )
        try:
            charge = gateway.charge(ChargeRequest(
                reference=invoice.number, amount=invoice.total, payment_token=decrypt(renewal.payment_token).decode(),
                description=_("%(seller)s %(plan)s plan (%(period)s), incl. PPN") % {
                    "seller": settings.BILLING_SELLER_NAME, "plan": plan.name, "period": period.label,
                },
                metadata={"invoice": invoice.number, "organization_id": str(organization.pk), "auto_renewal": "1"},
            ))
            invoice.gateway_reference = charge.gateway_reference
            invoice.save(update_fields=["gateway_reference", "updated_at"])
            invoice = sync_payment(invoice)
        except (GatewayError, PaymentAmountMismatch) as exc:
            logger.warning("Automatic renewal of organization %s failed: %s", organization.pk, exc)
            SubscriptionInvoice.objects.filter(pk=invoice.pk, status=Status.PENDING).update(status=Status.FAILED, updated_at=timezone.now())
            invoice.refresh_from_db()
        if invoice.status in (Status.PAID, Status.PENDING):  # pending: the webhook or a later sync settles it
            renewal.last_charged_for, renewal.failed_attempts, renewal.last_error = paid_until, 0, ""
            charged += 1
        else:
            failed += 1
            renewal.failed_attempts += 1
            renewal.last_error = invoice.status  # shown as its label, in the reader's language
            stopped = renewal.failed_attempts >= AutoRenewal.MAX_FAILED_ATTEMPTS
            if stopped:
                renewal.status, renewal.payment_token = AutoRenewal.Status.CANCELED, ""
            _notify_renewal_failed(organization, renewal.last_error, stopped)
        renewal.save()
    return charged, failed
