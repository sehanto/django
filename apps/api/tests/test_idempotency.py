"""API writes are all or nothing and can be repeated safely with an Idempotency-Key
(apps/api/idempotency.py); forms sent twice are processed once (apps/core/submissions.py)."""

import json
from datetime import date

import pytest
from django.urls import reverse
from django.utils import translation

from apps.api.models import IdempotentRequest
from apps.api.tests.test_api import key  # noqa: F401 - the fixture
from apps.invoicing.models import Invoice
from apps.ledger.models import JournalEntry

ENTRY = {"date": "2026-03-10", "description": "Setoran modal", "lines": [
    {"account": "1-1100", "debit": "1000"}, {"account": "3-1000", "credit": "1000"}]}


@pytest.fixture(autouse=True)
def english():
    with translation.override("en"):
        yield


def post(client, name, key, body, idempotency_key=None):  # noqa: F811
    headers = {"HTTP_AUTHORIZATION": f"Bearer {key}"}
    if idempotency_key is not None:
        headers["HTTP_IDEMPOTENCY_KEY"] = idempotency_key
    return client.post(reverse(f"api:{name}"), json.dumps(body), content_type="application/json", **headers)


def test_a_retry_with_the_same_key_records_nothing_twice(client, key, organization):  # noqa: F811
    first = post(client, "journal_entries", key, ENTRY, "c3a1b2d4-0001")
    again = post(client, "journal_entries", key, ENTRY, "c3a1b2d4-0001")
    assert first.status_code == again.status_code == 201 and again.json() == first.json()
    assert again["Idempotent-Replayed"] == "true" and not first.has_header("Idempotent-Replayed")
    assert JournalEntry.objects.filter(organization=organization).count() == 1
    post(client, "journal_entries", key, ENTRY, "c3a1b2d4-0002")  # another key: another entry
    post(client, "journal_entries", key, ENTRY)  # no key: as before
    assert JournalEntry.objects.filter(organization=organization).count() == 3


def test_a_key_is_for_one_request(client, key):  # noqa: F811
    assert post(client, "journal_entries", key, ENTRY, "k-1").status_code == 201
    other = post(client, "journal_entries", key, {**ENTRY, "description": "Lain"}, "k-1")
    assert other.status_code == 422 and "different request" in other.json()["error"]
    assert post(client, "journal_entries", key, ENTRY, "not valid!").status_code == 400
    assert post(client, "journal_entries", key, ENTRY, "x" * 101).status_code == 400


def test_a_failed_write_leaves_nothing_and_may_be_retried(client, key, organization, customer):  # noqa: F811
    organization.lock_date = date(2026, 3, 31)
    organization.save(update_fields=["lock_date"])
    body = {"kind": "sales", "contact": customer.pk, "date": "2026-03-10", "issue": True,
            "lines": [{"account": "4-1000", "description": "Kopi", "quantity": "1", "unit_price": "50000"}]}
    failed = post(client, "invoices", key, body, "inv-1")
    assert failed.status_code == 400 and "locked" in failed.json()["error"]
    assert not Invoice.objects.filter(organization=organization).exists()  # not even the draft
    assert not IdempotentRequest.objects.exists()  # nor the key: the retry runs
    organization.lock_date = None
    organization.save(update_fields=["lock_date"])
    assert post(client, "invoices", key, body, "inv-1").status_code == 201
    assert Invoice.objects.filter(organization=organization, status=Invoice.Status.ISSUED).count() == 1


def test_keys_older_than_a_day_are_forgotten(client, key):  # noqa: F811
    from datetime import timedelta

    from django.utils import timezone

    post(client, "journal_entries", key, ENTRY, "old")
    IdempotentRequest.objects.update(created_at=timezone.now() - timedelta(hours=25))
    assert not post(client, "journal_entries", key, ENTRY, "old").has_header("Idempotent-Replayed")


# --- Forms sent twice ---------------------------------------------------------------------------


def contact_form(token="masked-token-1"):
    return {"csrfmiddlewaretoken": token, "kind": "customer", "name": "PT Dua Kali", "payment_terms_days": "30",
            "document_language": "id", "is_active": "on"}


def test_a_form_sent_twice_is_processed_once(auth_client, organization):
    from apps.contacts.models import Contact

    first = auth_client.post(reverse("contacts:create"), contact_form())
    again = auth_client.post(reverse("contacts:create"), contact_form())
    assert first.status_code == again.status_code == 302 and again["Location"] == first["Location"]
    assert Contact.objects.filter(organization=organization, name="PT Dua Kali").count() == 1
    auth_client.post(reverse("contacts:create"), contact_form("masked-token-2"))  # the form rendered again
    assert Contact.objects.filter(organization=organization, name="PT Dua Kali").count() == 2


def test_a_form_with_errors_can_be_sent_again(auth_client, organization):
    form = {**contact_form(), "name": ""}
    assert auth_client.post(reverse("contacts:create"), form).status_code == 200
    assert auth_client.post(reverse("contacts:create"), form).status_code == 200  # shown again, not refused


def test_a_duplicate_of_a_submission_still_running_goes_back(auth_client, user):
    from django.core.cache import cache
    from django.test import RequestFactory

    from apps.core.submissions import PENDING, submission_key

    url = reverse("contacts:create")
    request = RequestFactory().post(url, contact_form())  # the same submission, as the first one saw it
    request.user, request.session = user, auth_client.session
    cache.set(submission_key(request), PENDING, 60)
    response = auth_client.post(url, contact_form(), HTTP_REFERER="http://testserver" + url)
    assert response.status_code == 302 and response["Location"].endswith(url)
