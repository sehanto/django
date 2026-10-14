"""The REST API: keys, roles and the main endpoints."""

import json
from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import translation

from apps.api.models import ApiKey
from apps.api.services import create_key
from apps.audit.models import AuditEvent
from apps.invoicing.models import Invoice
from apps.tenants.models import Membership
from apps.tenants.roles import Role

D = Decimal


@pytest.fixture
def key(organization, user):
    return create_key(organization=organization, user=user, name="Toko online")[1]


def call(client, method, name, key, *, args=(), body=None, **params):
    headers = {"HTTP_AUTHORIZATION": f"Bearer {key}"} if key else {}
    url = reverse(f"api:{name}", args=args)
    if method == "get":
        return client.get(url, params, **headers)
    return client.post(url, json.dumps(body or {}), content_type="application/json", **headers)


def test_requests_need_a_valid_key(client, key, organization, user):
    assert call(client, "get", "accounts", None).status_code == 401
    assert call(client, "get", "accounts", "eas_wrong").status_code == 401
    response = call(client, "get", "accounts", key)
    assert response.status_code == 200 and {"code": "1-1100", "name": "Bank"}.items() <= response.json()["results"][1].items()
    stored = ApiKey.objects.get()
    assert key not in (stored.key_hash, stored.prefix) and stored.last_used_at is not None
    Membership.objects.filter(organization=organization, user=user).update(role=Role.VIEWER)
    assert call(client, "post", "contacts", key, body={"name": "X"}).status_code == 403  # viewers only read
    Membership.objects.filter(organization=organization, user=user).delete()
    assert call(client, "get", "accounts", key).status_code == 401  # the member left


def test_a_sale_is_created_issued_and_paid(client, key, organization):
    contact = call(client, "post", "contacts", key, body={"name": "PT Pembeli", "email": "beli@example.com"})
    assert contact.status_code == 201
    created = call(client, "post", "invoices", key, body={
        "kind": "sales", "contact": contact.json()["id"], "date": "2026-03-05", "issue": True,
        "lines": [{"description": "Kopi", "account": "4-1000", "quantity": "2", "unit_price": "50000"}],
    })
    assert created.status_code == 201, created.content
    invoice = created.json()
    assert (invoice["status"], invoice["total"], len(invoice["lines"])) == ("issued", "100000.00", 1)
    paid = call(client, "post", "invoice_payments", key, args=[invoice["id"]], body={"date": "2026-03-10", "amount": "100000", "cash_account": "1-1100"})
    assert paid.status_code == 201 and Invoice.objects.get(pk=invoice["id"]).status == Invoice.Status.PAID
    listed = call(client, "get", "invoices", key, status="paid").json()
    assert listed["count"] == 1 and listed["results"][0]["number"] == invoice["number"]
    assert call(client, "get", "journal_entries", key, **{"from": "2026-03-01"}).json()["count"] == 2
    balances = {row["code"]: row["balance"] for row in call(client, "get", "trial_balance", key).json()["results"]}
    assert balances["1-1100"] == "100000.00" and balances["4-1000"] == "100000.00"


def test_errors_are_json(client, key):
    response = call(client, "post", "invoices", key, body={"kind": "sales", "lines": []})
    assert response.status_code == 400 and "lines" in response.json()["error"]
    assert call(client, "get", "invoice", key, args=[999999]).status_code == 404
    bad = call(client, "post", "contacts", key, body={"name": "Y", "npwp": "123"})
    assert bad.status_code == 400 and "npwp" in bad.json()["fields"]


@pytest.mark.parametrize("line, error", [
    ({"unit_price": "1e30"}, "unit_price"),            # would overflow the column
    ({"unit_price": "10.123"}, "unit_price"),          # rupiah have 2 decimal places
    ({"unit_price": True}, "unit_price"),              # not a number
    ({"unit_price": ["1"]}, "unit_price"),
    ({"unit_price": "NaN"}, "unit_price"),
    ({"quantity": "1.0001"}, "quantity"),              # 3 decimal places
    ({"discount_percent": "1000"}, "discount_percent"),
])
def test_numbers_are_checked_against_what_is_stored(client, key, organization, customer, line, error):
    body = {"kind": "sales", "contact": customer.pk, "date": "2026-03-05",
            "lines": [{"description": "Kopi", "account": "4-1000", "quantity": "1", "unit_price": "1000", **line}]}
    response = call(client, "post", "invoices", key, body=body)
    assert response.status_code == 400 and error in response.json()["error"]


def test_dates_and_filters_are_checked(client, key, customer):
    body = {"kind": "sales", "contact": customer.pk, "date": "2026-02-30",
            "lines": [{"description": "Kopi", "account": "4-1000", "unit_price": "1000"}]}
    response = call(client, "post", "invoices", key, body=body)  # well formed, no such day
    assert response.status_code == 400 and "date" in response.json()["error"]
    for name, params in (("invoices", {"kind": "gift"}), ("invoices", {"status": "lost"}), ("contacts", {"kind": "x\x00"})):
        response = call(client, "get", name, key, **params)
        assert response.status_code == 400 and "must be one of" in response.json()["error"]


def test_keys_are_managed_by_owners(auth_client, organization):
    with translation.override("en"):
        page = auth_client.post(reverse("api_keys:list"), {"name": "Kasir"})
    secret = page.context["secret"]
    assert secret.startswith("eas_") and AuditEvent.objects.filter(action="api_key_created").exists()
    key = ApiKey.objects.get(organization=organization)
    with translation.override("en"):
        auth_client.post(reverse("api_keys:revoke", args=[key.pk]))
    key.refresh_from_db()
    assert key.revoked_at is not None


def test_bills_are_paid_with_withholding_returned_and_journals_posted(client, key, organization, supplier, account):
    from apps.invoicing import services
    from apps.invoicing.models import InvoiceKind
    from apps.invoicing.services import InvoiceLineInput

    bill = services.issue_invoice(invoice=services.create_invoice(
        organization=organization, kind=InvoiceKind.PURCHASE, contact=supplier, date=date(2026, 3, 1),
        lines=[InvoiceLineInput("Servis", account("6-1700"), D("1"), D("1000000"))],
    ))
    paid = call(client, "post", "invoice_payments", key, args=[bill.pk], body={
        "date": "2026-03-10", "amount": "1000000", "cash_account": "1-1100",
        "income_tax": {"kind": "pph23", "base": "1000000", "rate": "2"},
    })
    assert paid.status_code == 201 and paid.json()["income_tax_withheld"] == "20000.00"
    line = call(client, "get", "invoice", key, args=[bill.pk]).json()["lines"][0]["id"]
    returned = call(client, "post", "invoice_returns", key, args=[bill.pk], body={
        "date": "2026-03-20", "reason": "Batal", "lines": [{"line": line, "quantity": "0.5"}], "cash_account": "1-1100",
    })
    assert returned.status_code == 201, returned.content
    assert returned.json()["total"] == "500000.00"
    entry = call(client, "post", "journal_entries", key, body={
        "date": "2026-03-31", "description": "Biaya bank", "lines": [
            {"account": "7-1000", "debit": "6500"}, {"account": "1-1100", "credit": "6500"},
        ],
    })
    assert entry.status_code == 201 and entry.json()["status"] == "posted"
    unbalanced = call(client, "post", "journal_entries", key, body={
        "date": "2026-03-31", "description": "x", "lines": [{"account": "7-1000", "debit": "1"}, {"account": "1-1100", "credit": "2"}],
    })
    assert unbalanced.status_code == 400
