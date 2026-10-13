"""Sending Coretax files through a provider (PJAP) and recording the faktur numbers issued."""

import base64
from datetime import date
from decimal import Decimal
from xml.etree import ElementTree as ET

import pytest
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone, translation

from apps.core.exceptions import InvalidStateTransition, ValidationFailed
from apps.invoicing import coretax_sync, services
from apps.invoicing.coretax_api import ConnectorName, CoretaxError, HttpConnector, SubmissionStatus
from apps.invoicing.models import CoretaxConnection, CoretaxSubmission, InvoiceKind
from apps.invoicing.services import InvoiceLineInput
from apps.taxes.models import TaxRate

D = Decimal


@pytest.fixture(autouse=True)
def english():
    with translation.override("en"):
        yield


@pytest.fixture
def books(pkp_organization, customer):
    pkp_organization.npwp = "0012345678901234"
    pkp_organization.save(update_fields=["npwp"])
    customer.npwp = "3174061502560010"
    customer.save()
    return pkp_organization


@pytest.fixture
def sale(books, customer, account):
    ppn = TaxRate.objects.filter(organization=books, dpp_numerator=11).first()
    return services.issue_invoice(invoice=services.create_invoice(
        organization=books, kind=InvoiceKind.SALES, contact=customer, date=date(2026, 3, 5),
        lines=[InvoiceLineInput("Kopi", account("4-1000"), D("1"), D("100000"), tax_rate=ppn)],
    ))


def test_sending_needs_a_connection_and_records_the_numbers(books, sale):
    with pytest.raises(InvalidStateTransition, match="Connect the organization"):
        coretax_sync.submit_invoices(organization=books, invoices=[sale], label=sale.number)
    coretax_sync.save_connection(organization=books, connector=ConnectorName.FAKE)
    submission = coretax_sync.submit_invoices(organization=books, invoices=[sale], label=sale.number)
    sale.refresh_from_db()
    assert submission.status == SubmissionStatus.ACCEPTED and list(submission.invoices.all()) == [sale]
    assert sale.tax_invoice_number.startswith("04") and len(sale.tax_invoice_number) == 17
    with pytest.raises(ValidationFailed, match="None of these invoices"):  # it has its number now
        coretax_sync.submit_invoices(organization=books, invoices=[sale], label=sale.number)


def test_the_connection_page_and_send_buttons(auth_client, books, sale):
    with translation.override("en"):
        response = auth_client.post(reverse("invoicing:coretax_connection"), {"connector": "http", "api_url": "http://plain.example.com", "token": "", "is_active": "on"})
        assert response.status_code == 200 and "HTTPS" in str(response.context["form"].non_field_errors())
        auth_client.post(reverse("invoicing:coretax_connection"), {"connector": "http", "api_url": "https://pjap.example.com/v1", "token": "rahasia-token", "is_active": "on"})
    connection = CoretaxConnection.objects.get(organization=books)
    assert connection.token and "rahasia-token" not in connection.token  # encrypted
    connection.connector = ConnectorName.FAKE
    connection.save()
    page = auth_client.get(reverse("invoicing:detail", args=[InvoiceKind.SALES, sale.pk]))
    assert page.context["coretax_connected"]
    with translation.override("en"):
        auth_client.post(reverse("invoicing:coretax_send_month", args=[2026, 3]))
    sale.refresh_from_db()
    assert sale.tax_invoice_number and CoretaxSubmission.objects.get(organization=books).label == "2026-03"


class Http:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        status, data = self.responses.pop(0)
        return type("R", (), {"status_code": status, "json": lambda self: data, "text": str(data)})()


def test_the_http_adapter_follows_its_contract(books, sale):
    http = Http((201, {"id": "sub-77", "status": "PENDING"}), (200, {"status": "accepted", "items": [{"reference": sale.number, "number": "040026-00000012345"}]}))
    connector = HttpConnector(url="https://pjap.example.com/v1", token="t", http=http)
    receipt = connector.submit(document_type="faktur_pk", tin="0012345678901234", file_name="f.xml", xml=b"<x/>")
    method, url, kwargs = http.calls[0]
    assert (method, url, receipt.reference, receipt.status) == ("POST", "https://pjap.example.com/v1/submissions", "sub-77", "pending")
    assert base64.b64decode(kwargs["json"]["xml"]) == b"<x/>" and kwargs["headers"]["Authorization"] == "Bearer t"
    result = connector.fetch("sub-77")
    assert result.status == "accepted" and result.items[0].number == "04002600000012345"
    with pytest.raises(CoretaxError, match="unknown status"):
        HttpConnector(url="https://x.example.com", token="t", http=Http((200, {"status": "weird"}))).fetch("1")


def test_pending_submissions_are_settled_by_the_nightly_job(books, sale):
    coretax_sync.save_connection(organization=books, connector=ConnectorName.FAKE)
    submission = CoretaxSubmission.objects.create(
        organization=books, document_type="faktur_pk", label="x", connector="fake", reference="fake-ct-unknown",
    )
    call_command("sync_coretax")
    submission.refresh_from_db()
    assert submission.status == SubmissionStatus.REJECTED  # the fake provider doesn't know it


def test_subscription_fakturs_are_exported_and_numbered(organization, settings, tmp_path):
    from apps.billing.models import SubscriptionInvoice
    from apps.tenants.models import Plan

    settings.BILLING_SELLER_NPWP, settings.CORETAX_CONNECTOR = "0999888777666555", "fake"
    plan, _ = Plan.objects.get_or_create(code="usaha", defaults={"name": "Usaha", "monthly_price": D("99000")})
    invoice = SubscriptionInvoice.objects.create(
        number="SUB-202603-000001", organization=organization, plan=plan, billing_period="monthly", buyer_name="Toko",
        buyer_npwp="0123456789012345", subtotal=D("99000"), tax_base=D("90750"), tax_amount=D("10890"), total=D("109890"),
        gateway="credit", status="pending",
    )
    SubscriptionInvoice.objects.filter(pk=invoice.pk).update(status="paid", paid_at=timezone.make_aware(timezone.datetime(2026, 3, 10, 9)))
    out = tmp_path / "f.xml"
    call_command("coretax_subscriptions", 2026, 3, output=str(out))
    good = ET.parse(out).getroot().find("ListOfTaxInvoice/TaxInvoice/ListOfGoodService/GoodService")
    assert [good.findtext(t) for t in ("TaxBase", "OtherTaxBase", "VAT", "Unit")] == ["99000", "90750", "10890", "UM.0024"]
    call_command("coretax_subscriptions", 2026, 3, send=True)
    invoice.refresh_from_db()
    assert len(invoice.tax_invoice_number) == 17
