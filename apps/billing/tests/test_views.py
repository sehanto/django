from datetime import timedelta
from decimal import Decimal
from unittest import mock

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.billing.gateways.base import GatewayPayment, PaymentStatus
from apps.billing.gateways.xendit import XenditGateway
from apps.billing.models import SubscriptionInvoice
from apps.tenants.roles import Role

CHECKOUT = reverse("billing:checkout")


def test_overview_shows_prices_and_is_for_billing_managers(auth_client, client, make_member, plans):
    html = auth_client.get(reverse("billing:overview")).content.decode()
    assert "Rp 99.000" in html and "Rp 109.890" in html  # price excl. PPN and amount incl. PPN
    client.force_login(make_member(Role.ACCOUNTANT))
    assert client.get(reverse("billing:overview")).status_code == 403
    assert client.post(CHECKOUT, {"plan": plans["growth"].pk, "period": "monthly"}).status_code == 403


def test_full_checkout_flow_with_fake_gateway(auth_client, free_org, plans):
    response = auth_client.post(CHECKOUT, {"plan": plans["growth"].pk, "period": "yearly"})
    invoice = SubscriptionInvoice.objects.get()
    assert response["Location"] == invoice.payment_url  # sent to the (fake) hosted payment page

    page = auth_client.get(invoice.payment_url)
    assert "Rp 2.763.900" in page.content.decode()  # 2.490.000 + PPN 273.900
    done = auth_client.post(invoice.payment_url, {"outcome": "paid"}, follow=True)
    assert "Pembayaran diterima" in done.content.decode()
    free_org.refresh_from_db()
    assert free_org.plan == plans["growth"]


def test_old_free_plan_switch_is_gone(auth_client, plans):
    assert auth_client.post("/organization/plan/", {"plan": plans["growth"].pk}).status_code == 404


def test_invalid_period_rejected(auth_client, plans):
    assert auth_client.post(CHECKOUT, {"plan": plans["growth"].pk, "period": "weekly"}).status_code == 400


def test_expiry_banner_for_billing_managers(auth_client, organization, plans):
    organization.plan, organization.plan_paid_until = plans["growth"], timezone.localdate() + timedelta(days=3)
    organization.save()
    assert "Perpanjang paket" in auth_client.get(reverse("dashboard:home")).content.decode()


@pytest.fixture
def xendit_settings(settings):
    settings.BILLING_GATEWAY = "xendit"
    settings.XENDIT_SECRET_KEY = "xnd_development_secret"
    settings.XENDIT_CALLBACK_TOKEN = "tok-123"
    return settings


def test_xendit_webhook_requires_token_and_rechecks_session(client, free_org, plans, xendit_settings):
    invoice = SubscriptionInvoice.objects.create(
        number="SUB-202610-000099", organization=free_org, plan=plans["starter"], billing_period="monthly",
        buyer_name=free_org.name, subtotal=Decimal("99000"), tax_base=Decimal("90750"), tax_amount=Decimal("10890"),
        total=Decimal("109890"), gateway="xendit", gateway_reference="ps-661f87c614802d6c402cd82d",
        payment_url="https://xen.to/abc", expires_at=timezone.now() + timedelta(hours=1),
    )
    url = reverse("billing:webhook", args=["xendit"])
    body = {"event": "payment_session.completed", "data": {"payment_session_id": invoice.gateway_reference}}

    forged = client.post(url, body, content_type="application/json", headers={"x-callback-token": "guess"})
    assert forged.status_code == 403

    paid = GatewayPayment(status=PaymentStatus.PAID, amount=Decimal("109890"), payment_id="py-1")
    with mock.patch.object(XenditGateway, "fetch_payment", return_value=paid) as fetch:
        ok = client.post(url, body, content_type="application/json", headers={"x-callback-token": "tok-123"})
    assert ok.status_code == 200
    fetch.assert_called_once_with(invoice.gateway_reference)  # status comes from the API, not the webhook body
    invoice.refresh_from_db()
    free_org.refresh_from_db()
    assert invoice.status == SubscriptionInvoice.Status.PAID and free_org.plan == plans["starter"]

    assert client.post(url, "not json", content_type="application/json", headers={"x-callback-token": "tok-123"}).status_code == 400
    unknown = {"data": {"payment_session_id": "ps-unknown"}}
    assert client.post(url, unknown, content_type="application/json", headers={"x-callback-token": "tok-123"}).status_code == 200
    assert client.post(reverse("billing:webhook", args=["fake"]), {}, content_type="application/json").status_code == 404
