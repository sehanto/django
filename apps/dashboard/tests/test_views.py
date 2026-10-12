"""HTTP-level tests: page rendering, tenant isolation, role permissions, full flows."""

from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse

from apps.contacts.models import Contact
from apps.invoicing import services as invoicing
from apps.invoicing.models import Invoice, InvoiceKind
from apps.invoicing.services import InvoiceLineInput
from apps.ledger.models import JournalEntry
from apps.tenants.roles import Role

D = Decimal

PAGES = [
    ("dashboard:home", {}),
    ("ledger:accounts", {}),
    ("ledger:account_create", {}),
    ("ledger:journals", {}),
    ("ledger:journal_create", {}),
    ("contacts:list", {}),
    ("contacts:create", {}),
    ("invoicing:list", {"kind": "sales"}),
    ("invoicing:list", {"kind": "purchase"}),
    ("invoicing:create", {"kind": "sales"}),
    ("invoicing:create", {"kind": "purchase"}),
    ("invoicing:payments", {}),
    ("invoicing:items", {}),
    ("invoicing:item_create", {}),
    ("taxes:rates", {}),
    ("taxes:rate_create", {}),
    ("taxes:pph_final", {}),
    ("reports:index", {}),
    ("reports:trial_balance", {}),
    ("reports:income_statement", {}),
    ("reports:balance_sheet", {}),
    ("reports:general_ledger", {}),
    ("reports:aging", {"kind": "sales"}),
    ("reports:vat", {}),
    ("tenants:settings", {}),
    ("tenants:members", {}),
    ("billing:overview", {}),
    ("accounts:profile", {}),
]


@pytest.mark.parametrize("language", ["id", "en"])
@pytest.mark.parametrize(("name", "kwargs"), PAGES)
def test_pages_render_in_both_languages(auth_client, user, name, kwargs, language):
    user.language = language
    user.save(update_fields=["language"])
    response = auth_client.get(reverse(name, kwargs=kwargs))
    assert response.status_code == 200
    assert f'<html lang="{language}">' in response.content.decode()


def test_anonymous_redirected_to_login(client, db):
    response = client.get(reverse("dashboard:home"))
    assert response.status_code == 302
    assert reverse("accounts:login") in response["Location"]


def test_signup_creates_organization_and_logs_in(client, db):
    response = client.post(reverse("accounts:signup"), {
        "full_name": "Dewi Lestari", "email": "dewi@example.com", "password": "Kuat-Sekali-99",
        "organization_name": "Batik Dewi", "legal_form": "perorangan",
    })
    assert response.status_code == 302
    assert client.get(reverse("dashboard:home")).context["current_organization"].name == "Batik Dewi"


def test_tenant_isolation(auth_client, other_organization):
    foreign_contact = Contact.objects.create(organization=other_organization, name="Rahasia", kind="customer")
    foreign_entry = JournalEntry.objects.create(organization=other_organization, number="JU/X", date=date(2026, 1, 1), description="x")
    foreign_invoice = Invoice.objects.create(
        organization=other_organization, kind=InvoiceKind.SALES, contact=foreign_contact, date=date(2026, 1, 1), due_date=date(2026, 1, 1)
    )
    assert auth_client.get(reverse("contacts:update", args=[foreign_contact.pk])).status_code == 404
    assert auth_client.get(reverse("ledger:journal_detail", args=[foreign_entry.pk])).status_code == 404
    assert auth_client.get(reverse("invoicing:detail", kwargs={"kind": "sales", "pk": foreign_invoice.pk})).status_code == 404
    assert auth_client.post(reverse("tenants:switch", args=[other_organization.pk])).status_code == 404
    assert "Rahasia" not in auth_client.get(reverse("contacts:list")).content.decode()


def test_viewer_cannot_write_and_accountant_cannot_manage(client, make_member):
    viewer = make_member(Role.VIEWER, email="viewer@example.com")
    client.force_login(viewer)
    assert client.get(reverse("reports:balance_sheet")).status_code == 200
    assert client.get(reverse("invoicing:create", kwargs={"kind": "sales"})).status_code == 403
    assert client.post(reverse("contacts:create"), {"name": "x"}).status_code == 403

    accountant = make_member(Role.ACCOUNTANT, email="acc@example.com")
    client.force_login(accountant)
    assert client.get(reverse("invoicing:create", kwargs={"kind": "sales"})).status_code == 200
    assert client.get(reverse("tenants:settings")).status_code == 403


def test_create_and_issue_invoice_via_http(auth_client, organization, customer, account):
    url = reverse("invoicing:create", kwargs={"kind": "sales"})
    response = auth_client.post(url, {
        "contact": customer.pk, "date": "2026-03-10", "due_date": "", "reference": "PO-1", "notes": "",
        "form-TOTAL_FORMS": "2", "form-INITIAL_FORMS": "0", "form-MIN_NUM_FORMS": "1", "form-MAX_NUM_FORMS": "1000",
        "form-0-description": "Kue lapis", "form-0-account": account("4-1000").pk, "form-0-quantity": "4",
        "form-0-unit_price": "25000", "form-0-discount_percent": "",
        "form-1-description": "", "form-1-account": account("4-1000").pk, "form-1-quantity": "1", "form-1-unit_price": "",
        "issue": "1",
    })
    assert response.status_code == 302, response.context["form"].errors if response.context else ""
    invoice = Invoice.objects.get(organization=organization)
    assert invoice.status == Invoice.Status.ISSUED
    assert invoice.total == D("100000")
    assert invoice.lines.count() == 1

    detail = auth_client.get(response["Location"])
    assert "Kue lapis" in detail.content.decode()

    pay = auth_client.post(reverse("invoicing:pay", kwargs={"kind": "sales", "pk": invoice.pk}), {
        "date": "2026-03-11", "amount": "100000", "cash_account": account("1-1000").pk, "method": "qris", "reference": "",
    })
    assert pay.status_code == 302
    invoice.refresh_from_db()
    assert invoice.status == Invoice.Status.PAID


def test_failed_issue_does_not_leave_a_draft_behind(auth_client, organization, customer, account):
    organization.lock_date = date(2026, 12, 31)
    organization.save()
    response = auth_client.post(reverse("invoicing:create", kwargs={"kind": "sales"}), {
        "contact": customer.pk, "date": "2026-03-10", "due_date": "", "reference": "", "notes": "",
        "form-TOTAL_FORMS": "1", "form-INITIAL_FORMS": "0", "form-MIN_NUM_FORMS": "1", "form-MAX_NUM_FORMS": "1000",
        "form-0-description": "Roti", "form-0-account": account("4-1000").pk, "form-0-quantity": "1", "form-0-unit_price": "1000",
        "issue": "1",
    })
    assert response.status_code == 200
    assert "dikunci" in response.content.decode()
    assert not Invoice.objects.filter(organization=organization).exists()


def test_manual_journal_via_http(auth_client, organization, account):
    response = auth_client.post(reverse("ledger:journal_create"), {
        "date": "2026-01-02", "description": "Modal", "reference": "",
        "form-TOTAL_FORMS": "2", "form-INITIAL_FORMS": "0", "form-MIN_NUM_FORMS": "2", "form-MAX_NUM_FORMS": "1000",
        "form-0-account": account("1-1000").pk, "form-0-debit": "1000000", "form-0-credit": "",
        "form-1-account": account("3-1000").pk, "form-1-debit": "", "form-1-credit": "1000000",
        "post": "1",
    })
    assert response.status_code == 302
    assert JournalEntry.objects.get(organization=organization).is_posted


def test_reports_render_with_data_and_export_csv(auth_client, organization, customer, account):
    invoice = invoicing.create_invoice(
        organization=organization, kind=InvoiceKind.SALES, contact=customer, date=date(2026, 3, 1),
        lines=[InvoiceLineInput("Jasa desain", account("4-1100"), D("1"), D("750000"))],
    )
    invoicing.issue_invoice(invoice=invoice)
    response = auth_client.get(reverse("reports:balance_sheet"), {"as_of": "2026-12-31"})
    assert response.context["report"].is_balanced
    assert "Rp 750.000" in response.content.decode()

    csv = auth_client.get(reverse("reports:income_statement"), {"date_from": "2026-01-01", "date_to": "2026-12-31", "download": "csv"})
    assert csv["Content-Type"].startswith("text/csv")
    assert 'filename="income-statement.csv"' in csv["Content-Disposition"]
    assert "Jasa" in csv.content.decode("utf-8-sig") or "Pendapatan" in csv.content.decode("utf-8-sig")

    ledger = auth_client.get(reverse("reports:general_ledger"), {"account": account("1-1200").pk, "date_from": "2026-01-01", "date_to": "2026-12-31"})
    assert ledger.context["report"].closing_balance == D("750000")
    assert auth_client.get(reverse("reports:aging", kwargs={"kind": "sales"})).context["report"].grand_total == D("750000")
    assert auth_client.get(reverse("invoicing:print", kwargs={"kind": "sales", "pk": invoice.pk})).status_code == 200


def test_printed_invoice_uses_contacts_document_language(auth_client, user, organization, customer, account):
    invoice = invoicing.issue_invoice(invoice=invoicing.create_invoice(
        organization=organization, kind=InvoiceKind.SALES, contact=customer, date=date(2026, 3, 1),
        lines=[InvoiceLineInput("Jasa", account("4-1000"), D("1"), D("1250000"))],
    ))
    user.language = "en"  # staff member works in English...
    user.save(update_fields=["language"])
    print_url = reverse("invoicing:print", kwargs={"kind": "sales", "pk": invoice.pk})

    html = auth_client.get(print_url).content.decode()  # ...customer document defaults to Indonesian
    assert '<html lang="id">' in html and "Ditagihkan kepada" in html and "Rp 1.250.000" in html

    customer.document_language = "en"
    customer.save(update_fields=["document_language"])
    response = auth_client.get(print_url)
    html = response.content.decode()
    assert response["Content-Language"] == "en"
    assert "Bill to" in html and "Rp 1,250,000" in html

    detail = auth_client.get(reverse("invoicing:detail", kwargs={"kind": "sales", "pk": invoice.pk})).content.decode()
    assert "Receive payment" in detail  # on-screen pages still follow the viewer


def test_amounts_in_error_messages_are_formatted(organization, customer, account):
    from django.utils import translation

    from apps.core.exceptions import ValidationFailed

    invoice = invoicing.issue_invoice(invoice=invoicing.create_invoice(
        organization=organization, kind=InvoiceKind.SALES, contact=customer, date=date(2026, 3, 1),
        lines=[InvoiceLineInput("Jasa", account("4-1000"), D("1"), D("1250000"))],
    ))
    with translation.override("en"), pytest.raises(ValidationFailed, match=r"\(Rp 1,250,000\)"):
        invoicing.record_payment(invoice=invoice, date=date(2026, 3, 2), amount=D("9999999"), cash_account=account("1-1000"))
