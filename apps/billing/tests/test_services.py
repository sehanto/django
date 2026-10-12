from datetime import date, timedelta
from decimal import Decimal
from unittest import mock

import pytest
from django.core import mail
from django.utils import timezone

from apps.billing import services
from apps.billing.gateways.base import GatewayError, PaymentStatus
from apps.billing.gateways.fake import FakeGateway
from apps.billing.models import SubscriptionInvoice
from apps.core.exceptions import PlanLimitExceeded, ValidationFailed
from apps.tenants.models import Organization
from apps.tenants.roles import Role
from apps.tenants.services import delete_organization

D = Decimal
Status = SubscriptionInvoice.Status


def pay(invoice, status=PaymentStatus.PAID, amount=None):
    """What the customer + Xendit webhook do, via the fake gateway."""
    FakeGateway().simulate(invoice.gateway_reference, status, amount)
    return services.handle_webhook(headers={}, payload={"reference": invoice.gateway_reference})


def test_checkout_creates_pending_invoice_with_payment_link(free_org, plans, user):
    invoice = services.start_checkout(organization=free_org, plan=plans["usaha"], period="monthly", created_by=user)
    assert invoice.status == Status.PENDING
    assert invoice.number.startswith("SUB-") and invoice.number.endswith(f"{invoice.pk:06d}")
    assert (invoice.subtotal, invoice.tax_amount, invoice.total) == (D("99000"), D("10890"), D("109890"))
    assert invoice.payment_url and invoice.is_payable
    assert invoice.buyer_name == free_org.name
    free_org.refresh_from_db()
    assert free_org.plan == plans["free"]  # nothing changes until paid


def test_pending_checkout_is_reused(free_org, plans):
    first = services.start_checkout(organization=free_org, plan=plans["pro"], period="yearly")
    again = services.start_checkout(organization=free_org, plan=plans["pro"], period="yearly")
    other = services.start_checkout(organization=free_org, plan=plans["pro"], period="monthly")
    assert first.pk == again.pk != other.pk


def test_free_plan_and_too_small_plan_are_rejected(free_org, plans, make_member):
    with pytest.raises(ValidationFailed):
        services.start_checkout(organization=free_org, plan=plans["free"], period="monthly")
    plans["usaha"].max_users = 1
    plans["usaha"].save()
    make_member(Role.VIEWER)
    with pytest.raises(PlanLimitExceeded):
        services.start_checkout(organization=free_org, plan=plans["usaha"], period="monthly")


def test_payment_activates_plan_once(free_org, plans):
    invoice = services.start_checkout(organization=free_org, plan=plans["pro"], period="monthly")
    paid = pay(invoice)
    today = timezone.localdate()
    assert paid.status == Status.PAID and paid.paid_at
    assert paid.coverage_start == today
    free_org.refresh_from_db()
    assert free_org.plan == plans["pro"]
    assert free_org.plan_paid_until == paid.coverage_end > today

    # Xendit retries webhooks: a duplicate must not extend the plan again.
    pay(invoice)
    free_org.refresh_from_db()
    assert free_org.plan_paid_until == paid.coverage_end


def test_renewing_same_plan_extends_without_losing_days(free_org, plans):
    first = pay(services.start_checkout(organization=free_org, plan=plans["usaha"], period="monthly"))
    second = pay(services.start_checkout(organization=free_org, plan=plans["usaha"], period="yearly"))
    assert second.coverage_start == first.coverage_end + timedelta(days=1)
    free_org.refresh_from_db()
    assert free_org.plan_paid_until == second.coverage_end


def test_amount_mismatch_does_not_activate(free_org, plans):
    invoice = services.start_checkout(organization=free_org, plan=plans["pro"], period="monthly")
    with pytest.raises(services.PaymentAmountMismatch):
        pay(invoice, amount=D("1000"))
    invoice.refresh_from_db()
    free_org.refresh_from_db()
    assert invoice.status == Status.PENDING and free_org.plan == plans["free"]


def test_expired_payment_link_closes_invoice(free_org, plans):
    invoice = pay(services.start_checkout(organization=free_org, plan=plans["pro"], period="monthly"), PaymentStatus.EXPIRED)
    assert invoice.status == Status.EXPIRED
    free_org.refresh_from_db()
    assert free_org.plan == plans["free"]


def test_gateway_failure_marks_invoice_failed(free_org, plans):
    with mock.patch.object(FakeGateway, "create_checkout", side_effect=GatewayError("down")):
        with pytest.raises(GatewayError):
            services.start_checkout(organization=free_org, plan=plans["pro"], period="monthly")
    assert SubscriptionInvoice.objects.get().status == Status.FAILED


def test_stale_checkouts_expire_but_late_payments_still_count(free_org, plans):
    stale = services.start_checkout(organization=free_org, plan=plans["pro"], period="monthly")
    late = services.start_checkout(organization=free_org, plan=plans["usaha"], period="monthly")
    FakeGateway().simulate(late.gateway_reference, PaymentStatus.PAID)
    later = timezone.now() + timedelta(days=2)
    services.expire_stale_checkouts(now=later)
    stale.refresh_from_db()
    late.refresh_from_db()
    assert stale.status == Status.EXPIRED
    assert late.status == Status.PAID


def test_unpaid_subscription_lapses_to_free_after_grace(organization, plans, settings):
    settings.BILLING_GRACE_DAYS = 7
    organization.plan, organization.plan_paid_until = plans["pro"], date(2026, 3, 31)
    organization.save()
    assert services.lapse_unpaid_subscriptions(today=date(2026, 4, 7)) == 0  # still in grace
    assert services.lapse_unpaid_subscriptions(today=date(2026, 4, 8)) == 1
    organization.refresh_from_db()
    assert organization.plan == plans["free"] and organization.plan_paid_until is None


def test_renewal_reminder_sent_once_to_billing_contacts_in_their_language(organization, plans, user, make_member, settings):
    settings.BILLING_REMINDER_DAYS = 7
    organization.plan, organization.plan_paid_until = plans["pro"], date(2026, 3, 31)
    organization.save()
    user.language = "en"
    user.save()
    make_member(Role.ACCOUNTANT, email="akuntan@example.com")  # not a billing contact
    admin = make_member(Role.ADMIN, email="admin@example.com")  # language stays Indonesian

    assert services.send_renewal_reminders(today=date(2026, 3, 20)) == 0  # too early
    assert services.send_renewal_reminders(today=date(2026, 3, 25)) == 2
    assert services.send_renewal_reminders(today=date(2026, 3, 26)) == 0  # once per period
    by_recipient = {m.to[0]: m for m in mail.outbox}
    assert set(by_recipient) == {user.email, admin.email}
    assert "ends on" in by_recipient[user.email].subject
    assert "berakhir" in by_recipient[admin.email].subject


def test_manual_bank_transfer_confirmation(free_org, plans, user):
    invoice = services.start_checkout(organization=free_org, plan=plans["usaha"], period="monthly")
    paid = services.mark_paid_manually(invoice=invoice, confirmed_by=user)
    assert paid.status == Status.PAID and paid.gateway_payment_id == f"manual:{user.email}"
    with pytest.raises(ValidationFailed):
        services.mark_paid_manually(invoice=paid, confirmed_by=user)


def test_sales_records_survive_tenant_deletion(free_org, plans):
    invoice = pay(services.start_checkout(organization=free_org, plan=plans["usaha"], period="monthly"))
    delete_organization(organization=Organization.objects.get(pk=free_org.pk))
    invoice.refresh_from_db()
    assert invoice.organization is None and invoice.buyer_name and invoice.status == Status.PAID
