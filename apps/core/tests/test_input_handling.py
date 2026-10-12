"""End-to-end checks that malformed or hostile input is rejected or normalized, never a 500."""

from datetime import date
from decimal import Decimal
from unittest import mock

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.billing import services as billing
from apps.billing.gateways.base import Checkout
from apps.billing.gateways.fake import FakeGateway
from apps.billing.models import SubscriptionInvoice
from apps.contacts.models import Contact
from apps.core.exceptions import ValidationFailed
from apps.ledger.services import JournalLineInput, create_journal_entry
from apps.tenants.models import Plan

D = Decimal


@pytest.mark.parametrize("url", [
    "/taxes/pph-final/?year=99999", "/taxes/pph-final/?year=-5", "/taxes/pph-final/?year=abc", "/reports/vat/?year=10000",
    "/ledger/journal/?status=bogus&source=%00&q=%00", "/contacts/?kind=%00&q=" + "x" * 5000, "/sales/?status=%00&q=%00",
    "/payments/?kind=%00", "/items/?q=%00", "/sales/new/?contact=abc",
    "/reports/income-statement/?date_from=0001-01-01&date_to=9999-12-31", "/reports/balance-sheet/?as_of=2026-02-30",
])
def test_malformed_query_parameters_never_crash(auth_client, url):
    assert auth_client.get(url).status_code == 200


def test_out_of_range_report_dates_are_reported_to_the_user(auth_client):
    response = auth_client.get("/reports/income-statement/?date_from=0001-01-01&date_to=2026-01-31")
    assert response.context["report"] is None
    assert response.context["form"].errors["date_from"]


def contact_post(**overrides):
    data = {
        "kind": "customer", "name": "PT Pelanggan", "npwp": "", "email": "", "phone": "", "address": "", "city": "",
        "payment_terms_days": "30", "document_language": "id", "notes": "", "is_active": "on",
    }
    return {**data, **overrides}


def test_contact_input_is_normalized_and_validated(auth_client, organization):
    response = auth_client.post(reverse("contacts:create"), contact_post(
        name="  PT‮ Hotel\x00  Merapi ", npwp="01.234.567.8-901.000", phone="0812-3456-7890",
        address="Jl. Kaliurang\r\n\r\n\r\nKm 5 ",
    ))
    assert response.status_code == 302
    contact = Contact.objects.get(organization=organization)
    assert contact.name == "PT Hotel Merapi"
    assert contact.npwp == "012345678901000"  # formatted NPWP accepted, stored as digits
    assert contact.address == "Jl. Kaliurang\n\nKm 5"

    bad = auth_client.post(reverse("contacts:create"), contact_post(npwp="12345", phone="call me", payment_terms_days="9999"))
    assert bad.status_code == 200
    assert set(bad.context["form"].errors) == {"npwp", "phone", "payment_terms_days"}


def test_line_count_is_capped(auth_client, account, customer):
    data = {
        "contact": customer.pk, "date": "2026-03-10", "due_date": "", "reference": "", "notes": "",
        "form-TOTAL_FORMS": "101", "form-INITIAL_FORMS": "0", "form-MIN_NUM_FORMS": "1", "form-MAX_NUM_FORMS": "1000",
    }
    for i in range(101):
        data |= {f"form-{i}-description": "x", f"form-{i}-account": account("4-1000").pk, f"form-{i}-quantity": "1", f"form-{i}-unit_price": "1"}
    response = auth_client.post(reverse("invoicing:create", kwargs={"kind": "sales"}), data)
    assert response.status_code == 200
    assert response.context["formset"].non_form_errors()


def test_implausible_dates_never_reach_the_books(organization, account):
    with pytest.raises(ValidationFailed, match="di luar rentang"):  # default language is Indonesian
        create_journal_entry(
            organization=organization, date=date(206, 3, 1), description="Salah ketik tahun",
            lines=[JournalLineInput(account("1-1000"), debit=D("1")), JournalLineInput(account("3-1000"), credit=D("1"))],
        )


def test_model_rules_apply_in_forms(auth_client, organization):
    item = auth_client.post(reverse("invoicing:item_create"), {
        "code": "KOPI 01;", "name": "Kopi", "type": "goods", "unit": "kg", "sale_price": "-1", "purchase_price": "0", "is_active": "on",
    })
    assert {"code", "sale_price"} <= set(item.context["form"].errors)

    rate = auth_client.post(reverse("taxes:rate_create"), {"name": "Aneh", "rate": "12", "dpp_numerator": "13", "dpp_denominator": "12", "is_active": "on"})
    assert "dpp_numerator" in rate.context["form"].errors

    settings_form = auth_client.post(reverse("tenants:settings"), {
        "name": organization.name, "legal_form": organization.legal_form, "npwp": "", "nib": "1234 5678 90123",
        "address": "", "city": "", "phone": "", "email": "", "uses_pph_final": "on",
        "lock_date": (timezone.localdate().replace(year=timezone.localdate().year + 1)).isoformat(),
    })
    assert "lock_date" in settings_form.context["form"].errors
    assert settings_form.context["form"].cleaned_data["nib"] == "1234567890123"  # spaces removed


def test_csv_export_neutralizes_formulas(auth_client, organization, account):
    revenue = account("4-1000")
    revenue.name = '=HYPERLINK("http://evil.example","Klik")'
    revenue.save()
    create_journal_entry(
        organization=organization, date=date(2026, 3, 1), description="Penjualan",
        lines=[JournalLineInput(account("1-1000"), debit=D("10")), JournalLineInput(revenue, credit=D("10"))],
    )
    csv = auth_client.get(reverse("reports:trial_balance"), {"as_of": "2026-12-31", "download": "csv"}).content.decode("utf-8-sig")
    assert '"\'=HYPERLINK(' in csv  # escaped (and quoted by the CSV writer because of the inner quotes)
    assert ';=HYPERLINK(' not in csv and ';"=HYPERLINK(' not in csv


def test_webhook_rejects_non_objects_and_odd_references(client, organization):
    url = reverse("billing:webhook", args=["fake"])
    assert client.post(url, "[1, 2]", content_type="application/json").status_code == 400
    assert client.post(url, '"text"', content_type="application/json").status_code == 400
    for reference in [{"$gt": ""}, ["a"], "x" * 200, "a b;c"]:
        assert client.post(url, {"reference": reference}, content_type="application/json").status_code == 200
    assert not SubscriptionInvoice.objects.filter(status="paid").exists()


def test_unsafe_payment_links_from_the_gateway_are_refused(organization):
    pro = Plan.objects.create(code="pro-x", name="Pro X", monthly_price=D("1000"), yearly_price=D("10000"))
    hostile = Checkout(gateway_reference="ps-1", payment_url="javascript:alert(1)", expires_at=None)
    with mock.patch.object(FakeGateway, "create_checkout", return_value=hostile):
        with pytest.raises(billing.GatewayError):
            billing.start_checkout(organization=organization, plan=pro, period="monthly")
    assert SubscriptionInvoice.objects.get().status == SubscriptionInvoice.Status.FAILED


def test_checkout_with_non_numeric_plan_is_404(auth_client):
    assert auth_client.post(reverse("billing:checkout"), {"plan": "abc", "period": "monthly"}).status_code == 404
