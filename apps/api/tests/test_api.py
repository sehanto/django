"""The REST API: keys, roles and the main endpoints."""

import json
from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone, translation

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
