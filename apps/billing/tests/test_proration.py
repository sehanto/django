"""Proration: unused paid time on the current plan is credited when switching plans."""

from datetime import date, timedelta
from decimal import Decimal
from unittest import mock

import pytest
from django.urls import reverse

from apps.billing import services
from apps.billing.domain import BillingPeriod, Credit, PaidPeriod, compute_charge, switch_quote, unused_credit
from apps.billing.gateways.base import GatewayName, PaymentStatus
from apps.billing.gateways.fake import FakeGateway
from apps.billing.models import SubscriptionInvoice

D = Decimal
Status = SubscriptionInvoice.Status
TODAY = date(2026, 10, 12)


# --- Domain -------------------------------------------------------------------------------


def test_unused_credit_counts_days_after_today():
    october = PaidPeriod(date(2026, 10, 1), date(2026, 10, 31), D("99000"))
    assert unused_credit([october], TODAY) == Credit(D("60677"), 19)  # 13-31 Oct, 99.000 × 19/31
    assert unused_credit([october], date(2026, 10, 31)).amount == 0  # last day is used
    assert unused_credit([october], date(2026, 9, 20)).days == 31  # renewal paid ahead: all unused


def test_stacked_renewals_are_all_credited():
    periods = [PaidPeriod(date(2026, 10, 1), date(2026, 10, 31), D("99000")),
               PaidPeriod(date(2026, 11, 1), date(2026, 11, 30), D("99000"))]
    credit = unused_credit(periods, TODAY)
    assert credit.days == 49 and credit.amount == D("159677")  # 60.677 + a full November


def test_ppn_is_charged_on_the_net_amount():
    charge = compute_charge(D("249000"), credit=D("60677"))
    assert (charge.net, charge.tax, charge.total) == (D("188323"), D("20715"), D("209038"))


def test_large_credit_switches_for_free_and_extends():
    quote = switch_quote(price=D("99000"), credit=Credit(D("545753"), 80), period=BillingPeriod.MONTHLY, starts_on=TODAY)
    assert not quote.needs_payment and quote.charge.credit == D("99000")
    assert quote.extension_days == 139  # 446.753 leftover at 99.000/31 days a day
    assert quote.covered_until == date(2027, 3, 30)  # 31 days + 139 extra, shown to the customer


# --- Services ----------------------------------------------------------------------------


@pytest.fixture
def frozen_today():
    with mock.patch("django.utils.timezone.localdate", return_value=TODAY):
        yield TODAY


def pay(invoice):
    FakeGateway().simulate(invoice.gateway_reference, PaymentStatus.PAID)
    return services.handle_webhook(headers={}, payload={"reference": invoice.gateway_reference})


def subscribe(organization, plan, period="monthly"):
    invoice = pay(services.start_checkout(organization=organization, plan=plan, period=period))
    organization.refresh_from_db()  # each request loads the organization afresh; mirror that
    return invoice


def test_upgrade_mid_period_pays_only_the_difference(free_org, plans):
    with mock.patch("django.utils.timezone.localdate", return_value=date(2026, 10, 1)):
        usaha = subscribe(free_org, plans["usaha"])  # 1–31 Oct
    with mock.patch("django.utils.timezone.localdate", return_value=TODAY):
        invoice = services.start_checkout(organization=free_org, plan=plans["pro"], period="monthly")
        assert (invoice.credit_amount, invoice.credited_days, invoice.total) == (D("60677"), 19, D("209038"))
        assert invoice.credited_plan == plans["usaha"]
        paid = pay(invoice)
    free_org.refresh_from_db()
    assert free_org.plan == plans["pro"]
    assert (paid.coverage_start, paid.coverage_end) == (TODAY, date(2026, 11, 11))  # a full month of Pro
    usaha.refresh_from_db()
    assert usaha.superseded_on == TODAY  # its remaining days were used up by the credit


def test_downgrade_covered_by_credit_switches_immediately(free_org, plans):
    with mock.patch("django.utils.timezone.localdate", return_value=date(2026, 1, 1)):
        subscribe(free_org, plans["pro"], "yearly")  # 2026 paid in full
    with mock.patch("django.utils.timezone.localdate", return_value=TODAY):
        invoice = services.start_checkout(organization=free_org, plan=plans["usaha"], period="monthly")
    assert invoice.status == Status.PAID and invoice.total == 0 and invoice.gateway == GatewayName.CREDIT
    assert invoice.extension_days == 139
    free_org.refresh_from_db()
    assert free_org.plan == plans["usaha"] and free_org.plan_paid_until == date(2027, 3, 30)


def test_credit_is_never_used_twice(free_org, plans):
    with mock.patch("django.utils.timezone.localdate", return_value=date(2026, 1, 1)):
        subscribe(free_org, plans["pro"], "yearly")  # 2026 paid: Rp2.490.000
    with mock.patch("django.utils.timezone.localdate", return_value=TODAY):
        pro_credit = services.switch_credit(free_org, plans["usaha"])
        services.start_checkout(organization=free_org, plan=plans["usaha"], period="monthly")  # free switch
        free_org.refresh_from_db()
        back_credit = services.switch_credit(free_org, plans["pro"])
    # The superseded Pro year is not counted again: the credit comes only from the Usaha
    # coverage, which holds the same money (counted twice it would be ~Rp1.088.000).
    assert back_credit.amount <= pro_credit.amount
    assert back_credit.amount > pro_credit.amount - D("5000")  # less about one day's use


def test_stale_credit_at_payment_buys_proportional_time(free_org, plans):
    with mock.patch("django.utils.timezone.localdate", return_value=date(2026, 10, 1)):
        subscribe(free_org, plans["usaha"])
    with mock.patch("django.utils.timezone.localdate", return_value=TODAY):
        to_pro = services.start_checkout(organization=free_org, plan=plans["pro"], period="monthly")
        to_pro_yearly = services.start_checkout(organization=free_org, plan=plans["pro"], period="yearly")  # second tab
        pay(to_pro_yearly)  # this one uses the Usaha credit
        stale = pay(to_pro)  # the credit it was quoted is gone
    share = stale.net_subtotal / stale.subtotal  # 188.323 / 249.000
    assert stale.coverage_start == date(2027, 10, 12)  # renews after the yearly period...
    assert (stale.coverage_end - stale.coverage_start).days + 1 == int(31 * share)  # ...for the share actually paid


def test_no_credit_when_renewing_or_without_paid_time(free_org, plans, organization):
    with mock.patch("django.utils.timezone.localdate", return_value=date(2026, 10, 1)):
        subscribe(free_org, plans["usaha"])
    with mock.patch("django.utils.timezone.localdate", return_value=TODAY):
        assert services.start_checkout(organization=free_org, plan=plans["usaha"], period="yearly").credit_amount == 0
    with mock.patch("django.utils.timezone.localdate", return_value=date(2026, 11, 3)):  # grace period
        assert services.start_checkout(organization=free_org, plan=plans["pro"], period="monthly").credit_amount == 0


def test_grandfathered_plan_without_invoices_gets_no_credit(free_org, plans):
    free_org.plan, free_org.plan_paid_until = plans["pro"], TODAY + timedelta(days=20)
    free_org.save()
    with mock.patch("django.utils.timezone.localdate", return_value=TODAY):
        assert services.start_checkout(organization=free_org, plan=plans["usaha"], period="monthly").credit_amount == 0


# --- UI ----------------------------------------------------------------------------------


def test_plan_cards_and_receipt_show_the_credit(auth_client, organization, plans):
    organization.plan, organization.plan_paid_until = plans["free"], None
    organization.save()
    with mock.patch("django.utils.timezone.localdate", return_value=date(2026, 10, 1)):
        subscribe(organization, plans["usaha"])
    with mock.patch("django.utils.timezone.localdate", return_value=TODAY):
        html = auth_client.get(reverse("billing:overview")).content.decode()
        assert "Rp 60.677" in html and "Rp 209.038" in html  # credit and the net amount due
        invoice = services.start_checkout(organization=organization, plan=plans["pro"], period="monthly")
        receipt = auth_client.get(reverse("billing:invoice", args=[invoice.pk])).content.decode()
    assert "-Rp 60.677" in receipt and "19 hari" in receipt and "Rp 188.323" in receipt
