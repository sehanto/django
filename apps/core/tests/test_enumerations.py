"""Choice values come from enumerations, never from string literals scattered in templates."""

import re
from datetime import date
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.urls import reverse

from apps.invoicing import services
from apps.invoicing.models import Invoice, InvoiceKind, InvoiceStatusFilter
from apps.invoicing.services import InvoiceLineInput

TEMPLATES = Path(settings.BASE_DIR) / "templates"
CHOICE_FIELDS = r"(status|state|role|source|kind|period|method|type|gateway|legal_form|category)"
LITERAL_COMPARISON = re.compile(r"\b" + CHOICE_FIELDS + r"\s*(==|!=)\s*['\"]")
LITERAL_URL_KIND = re.compile(r"\{%\s*url\s+'invoicing:\w+'\s+'(sales|purchase)'")


def test_templates_compare_choices_with_enum_members():
    offenders = [
        f"{path.relative_to(TEMPLATES)}:{number}"
        for path in TEMPLATES.rglob("*.html")
        for number, line in enumerate(path.read_text().splitlines(), 1)
        if LITERAL_COMPARISON.search(line) or LITERAL_URL_KIND.search(line)
    ]
    assert offenders == [], "Compare against enum members (e.g. invoice.Status.PAID), not quoted values"


def test_status_filter_offers_every_status_plus_overdue():
    assert InvoiceStatusFilter.values == [*Invoice.Status.values, "overdue"]


def test_overdue_filter_lists_open_invoices_past_due(auth_client, organization, customer, account):
    invoice = services.create_invoice(
        organization=organization, kind=InvoiceKind.SALES, contact=customer, date=date(2026, 3, 10),
        lines=[InvoiceLineInput("Kopi bubuk 1kg", account("4-1000"), Decimal("1"), Decimal("100000"))],
    )
    services.issue_invoice(invoice=invoice)
    url = reverse("invoicing:list", args=[InvoiceKind.SALES])

    overdue = auth_client.get(url, {"status": InvoiceStatusFilter.OVERDUE})
    assert list(overdue.context["object_list"]) == [invoice]
    assert f'value="{InvoiceStatusFilter.OVERDUE}" selected' in overdue.content.decode()
    assert list(auth_client.get(url, {"status": InvoiceStatusFilter.PAID}).context["object_list"]) == []


def test_payment_list_offers_each_invoice_kind(auth_client):
    html = auth_client.get(reverse("invoicing:payments"), {"kind": InvoiceKind.PURCHASE}).content.decode()
    assert f'<option value="{InvoiceKind.SALES}" >' in html
    assert f'<option value="{InvoiceKind.PURCHASE}" selected>' in html
