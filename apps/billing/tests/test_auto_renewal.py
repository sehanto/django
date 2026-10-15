"""Automatic renewal with a saved payment method."""

from datetime import date, timedelta

import pytest
from django.core import mail
from django.urls import reverse
from django.utils import timezone, translation

from apps.billing import services
from apps.billing.gateways.fake import DECLINED_TOKEN
from apps.billing.models import AutoRenewal, SubscriptionInvoice
from apps.core.crypto import encrypt


@pytest.fixture(autouse=True)
def english():
    with translation.override("en"):
        yield


@pytest.fixture
def paid_org(organization, plans):
    organization.plan, organization.plan_paid_until = plans["starter"], timezone.localdate() + timedelta(days=1)
    organization.save()
    return organization


def test_turning_it_on_saves_a_payment_method(auth_client, paid_org):
    with translation.override("en"):
        response = auth_client.post(reverse("billing:auto_renewal_start"), {"period": "monthly"})
        renewal = AutoRenewal.objects.get(organization=paid_org)
        assert renewal.status == AutoRenewal.Status.SETUP and response.url == renewal.setup_url
        auth_client.post(renewal.setup_url)  # the fake gateway's page
    renewal.refresh_from_db()
    assert renewal.status == AutoRenewal.Status.ACTIVE and renewal.payment_token and "fake-token" not in renewal.payment_token
    assert auth_client.get(reverse("billing:overview")).context["renewal"] == renewal
    with translation.override("en"):
        auth_client.post(reverse("billing:auto_renewal_cancel"))
    renewal.refresh_from_db()
    assert renewal.status == AutoRenewal.Status.CANCELED and not renewal.payment_token


def active(organization, token="fake-token-ok"):
    return AutoRenewal.objects.create(
        organization=organization, billing_period="monthly", status=AutoRenewal.Status.ACTIVE, gateway="fake",
        payment_token=encrypt(token.encode()), activated_at=timezone.now(),
    )


def test_due_renewals_are_charged_once_per_period(paid_org, plans):
    renewal = active(paid_org)
    paid_until = paid_org.plan_paid_until
    assert services.charge_auto_renewals() == (1, 0)
    invoice = SubscriptionInvoice.objects.get(organization=paid_org)
    paid_org.refresh_from_db()
    assert invoice.status == SubscriptionInvoice.Status.PAID and invoice.gateway_reference.startswith("fake-charge-")
    assert paid_org.plan_paid_until > paid_until
    assert services.charge_auto_renewals() == (0, 0)  # not due again until the new period ends
    renewal.refresh_from_db()
    assert renewal.last_charged_for == paid_until
    assert services.send_renewal_reminders() == 0  # renewing automatically: no reminder


def test_declined_charges_notify_and_stop_after_three(paid_org, user):
    user.language = "en"  # emails go out in each recipient's language
    user.save(update_fields=["language"])
    renewal = active(paid_org, DECLINED_TOKEN)
    for attempt in range(1, 4):
        mail.outbox.clear()
        assert services.charge_auto_renewals() == (0, 1)
        renewal.refresh_from_db()
        assert renewal.failed_attempts == attempt and len(mail.outbox) == 1
    assert renewal.status == AutoRenewal.Status.CANCELED and "turned off" in mail.outbox[0].body
    assert SubscriptionInvoice.objects.filter(organization=paid_org, status=SubscriptionInvoice.Status.FAILED).count() == 3
    paid_org.refresh_from_db()
    assert paid_org.plan_paid_until == timezone.localdate() + timedelta(days=1)


def test_renewals_not_yet_due_or_on_the_free_plan_are_left_alone(organization, plans):
    organization.plan, organization.plan_paid_until = plans["starter"], timezone.localdate() + timedelta(days=20)
    organization.save()
    active(organization)
    assert services.charge_auto_renewals() == (0, 0)
    assert services.charge_auto_renewals(today=date.today() + timedelta(days=19)) == (1, 0)
