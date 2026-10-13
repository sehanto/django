"""Subscription invoices are immutable sales records in the database (billing migration 0006).

The legitimate lifecycle runs through the services (see test_services / test_proration);
these tests bypass them to prove the PostgreSQL guard.
"""

from contextlib import contextmanager
from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.billing import services
from apps.billing.models import SubscriptionInvoice
from apps.billing.tests.test_services import pay

Status = SubscriptionInvoice.Status


@contextmanager
def refused():
    with pytest.raises(IntegrityError), transaction.atomic():
        yield


@pytest.fixture
def pending(free_org, plans):
    return services.start_checkout(organization=free_org, plan=plans["usaha"], period="monthly")


@pytest.fixture
def paid(pending):
    pay(pending)
    pending.refresh_from_db()
    assert pending.status == Status.PAID
    return pending


def update(invoice, **changes):
    SubscriptionInvoice.objects.filter(pk=invoice.pk).update(**changes)


def test_terms_are_fixed_from_the_start(pending, plans):
    for change in ({"total": Decimal("1")}, {"plan": plans["pro"]}, {"buyer_name": "Orang Lain"},
                   {"billing_period": "yearly"}, {"credit_amount": Decimal("5")}, {"gateway": "credit"}):
        with refused():
            update(pending, **change)


def test_paid_invoices_are_final(paid):
    for change in ({"status": Status.PENDING}, {"status": Status.EXPIRED}, {"paid_at": timezone.now()},
                   {"coverage_end": paid.coverage_end + timedelta(days=365)}, {"gateway_payment_id": "forged"},
                   {"payment_url": "https://evil.example/"}, {"number": "SUB-000000-000001"}):
        with refused():
            update(paid, **change)
    with refused():
        paid.delete()


def test_paid_needs_a_payment_time_and_ended_early_is_set_once(paid, free_org, plans):
    update(paid, superseded_on=timezone.localdate())  # a plan switch ends it early: allowed once
    with refused():
        update(paid, superseded_on=None)
    unpaid = services.start_checkout(organization=free_org, plan=plans["pro"], period="monthly")
    with refused():
        update(unpaid, status=Status.PAID)  # no paid_at
    with refused():
        update(unpaid, superseded_on=timezone.localdate())  # not paid


def test_closed_checkouts_can_still_be_confirmed_by_support(pending, user):
    update(pending, status=Status.EXPIRED)
    with refused():
        update(pending, payment_url="https://xen.to/new")  # the checkout is closed
    with refused():
        update(pending, status=Status.PENDING)
    confirmed = services.mark_paid_manually(invoice=pending, confirmed_by=user)
    assert confirmed.status == Status.PAID and confirmed.gateway_payment_id == f"manual:{user.email}"


def test_new_invoices_start_unpaid(free_org, plans):
    terms = dict(
        number="SUB-X", organization=free_org, plan=plans["usaha"], billing_period="monthly", buyer_name="x",
        subtotal=Decimal("99000"), tax_base=Decimal("90750"), tax_amount=Decimal("10890"), total=Decimal("109890"),
    )
    with refused():
        SubscriptionInvoice.objects.create(**terms, gateway="fake", status=Status.PAID, paid_at=timezone.now())
    with refused():
        SubscriptionInvoice.objects.create(**terms, gateway="fake", gateway_payment_id="pay-1")
