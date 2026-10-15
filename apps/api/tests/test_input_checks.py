"""What the API refuses: over-long text (never cut silently), document numbers with characters
that don't belong in one, identifiers in the wrong format, and key guessing (failed
authentications per address)."""

from datetime import date
from decimal import Decimal

import pytest
from django.utils import translation

from apps.api.tests.test_api import call, key  # noqa: F401 - the fixture
from apps.core.exceptions import ValidationFailed
from apps.core.validators import document_number_validator, ensure_document_number

D = Decimal


@pytest.fixture(autouse=True)
def english():
    """Messages are matched in English (the default language is Indonesian)."""
    with translation.override("en"):
        yield


@pytest.mark.parametrize("number", ["PIB-000123/2026", "SKB 123/WPJ.04/2025", "PJKEK#12", "BPPU-00012", "DGT (SKD) 1:2", "A&B, 12"])
def test_document_numbers_take_the_separators_real_ones_use(number):
    document_number_validator(number)
    assert ensure_document_number(f"  {number} ", max_length=50, missing="Enter it.") == number


@pytest.mark.parametrize("number", ["<b>1</b>", 'BP"1', "12;DROP", "-123", "a=b", "x\x00y", "BP'1"])
def test_anything_else_is_refused(number):
    with pytest.raises(ValidationFailed, match="Use letters, digits"):
        ensure_document_number(number, max_length=50, missing="Enter it.")


def test_an_empty_or_long_number_asks_for_it():
    for number in ("", "   ", "1" * 51):
        with pytest.raises(ValidationFailed, match="Enter it."):
            ensure_document_number(number, max_length=50, missing="Enter it.")


def test_text_longer_than_its_column_is_refused_not_cut(client, key):  # noqa: F811
    body = {"date": "2026-03-10", "description": "x" * 256, "lines": [
        {"account": "1-1100", "debit": "1000"}, {"account": "3-1000", "credit": "1000"}]}
    response = call(client, "post", "journal_entries", key, body=body)
    assert response.status_code == 400 and "at most 255 characters" in response.json()["error"]
    body["description"] = "x" * 255
    assert call(client, "post", "journal_entries", key, body=body).status_code == 201


def test_a_bukti_potong_number_is_checked_on_payment(client, key, organization, supplier, account):  # noqa: F811
    from apps.invoicing import services
    from apps.invoicing.models import InvoiceKind
    from apps.invoicing.services import InvoiceLineInput

    bill = services.issue_invoice(invoice=services.create_invoice(
        organization=organization, kind=InvoiceKind.PURCHASE, contact=supplier, date=date(2026, 3, 1),
        lines=[InvoiceLineInput("Servis", account("6-1700"), D("1"), D("1000000"))],
    ))
    payment = {"date": "2026-03-10", "amount": "1000000", "cash_account": "1-1100",
               "income_tax": {"kind": "pph23", "base": "1000000", "rate": "2", "slip": "<BP>1"}}
    response = call(client, "post", "invoice_payments", key, args=[bill.pk], body=payment)
    assert response.status_code == 400 and "Use letters, digits" in response.json()["error"]
    payment["income_tax"]["slip"] = "BPPU-0001"
    assert call(client, "post", "invoice_payments", key, args=[bill.pk], body=payment).status_code == 201


def test_a_foreign_tax_id_has_its_format(client, key):  # noqa: F811
    response = call(client, "post", "contacts", key, body={"name": "Acme Pte Ltd", "country": "SGP", "foreign_tin": "123;456"})
    assert response.status_code == 400 and "foreign_tin" in response.json()["fields"]
    response = call(client, "post", "contacts", key, body={"name": "Acme Pte Ltd", "country": "SGP", "foreign_tin": "201912345K"})
    assert response.status_code == 201


def test_guessing_keys_is_throttled_per_address(client, key, settings):  # noqa: F811
    settings.API_AUTH_FAILURES_PER_HOUR = 3
    for _ in range(3):
        assert call(client, "get", "accounts", "eas_wrong").status_code == 401
    assert call(client, "get", "accounts", "eas_wrong").status_code == 429
    assert call(client, "get", "accounts", key).status_code == 429  # a right guess now looks the same as a wrong one
    other = {"REMOTE_ADDR": "203.0.113.9", "HTTP_AUTHORIZATION": f"Bearer {key}"}
    from django.urls import reverse
    assert client.get(reverse("api:accounts"), **other).status_code == 200  # another address isn't affected


@pytest.mark.parametrize(("model", "field"), [
    ("payroll.Employee", "passport_number"), ("payroll.Employee", "foreign_tin"), ("contacts.Contact", "foreign_tin"),
])
def test_identity_numbers_have_their_format_wherever_they_are_kept(model, field):
    from django.apps import apps
    from django.core.exceptions import ValidationError

    model_field = apps.get_model(model)._meta.get_field(field)
    model_field.run_validators("A1234567")
    with pytest.raises(ValidationError):
        model_field.run_validators("A12 <34>")
