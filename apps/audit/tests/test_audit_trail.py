"""The audit trail: every change in a tenant table recorded by the database, append-only and
hash-chained, with who made it; and the guards added with it (document numbers, a previous
employer's income, Coretax submissions)."""

import json
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from unittest import mock

import pytest
from django.apps import apps
from django.core.management import CommandError, call_command
from django.db import IntegrityError, connection, transaction
from django.urls import reverse
from django.utils import translation

from apps.api.services import create_key
from apps.audit import context
from apps.audit.models import RecordChange
from apps.audit.selectors import field_changes, trail_head, tracked_tables, verify_trail
from apps.contacts.models import Contact
from apps.core.models import DocumentSequence
from apps.core.sequences import next_document_number
from apps.invoicing.coretax_api import SubmissionStatus
from apps.invoicing.models import CoretaxSubmission
from apps.ledger.models import JournalEntry
from apps.ledger.services import JournalLineInput, create_journal_entry
from apps.payroll import services as payroll
from apps.payroll.models import Employee, PriorIncome
from apps.payroll.services import PayInput
from apps.tenants import services as tenants
from apps.tenants.roles import Role

D = Decimal
# Tenant data that isn't in the trail, and why.
UNTRACKED = {
    "core_documentsequence": "a counter; guarded instead, and every number is in its document's row",
    "tenants_plan": "the platform's price list, not tenant data",
    "api_idempotentrequest": "API responses kept 24 hours to answer retries; the documents they created are tracked",
}


@pytest.fixture(autouse=True)
def english():
    with translation.override("en"):
        yield


def changes(organization, table, **filters):
    return list(RecordChange.objects.filter(organization=organization, table_name=table, **filters).order_by("sequence"))


@contextmanager
def guard_disabled():
    """What someone with the owner role (or a superuser) could do: the chain still tells."""
    with connection.cursor() as cursor:
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
        cursor.execute("ALTER TABLE audit_recordchange DISABLE TRIGGER audit_recordchange_guard")
        try:
            yield cursor
        finally:
            cursor.execute("ALTER TABLE audit_recordchange ENABLE TRIGGER audit_recordchange_guard")
            cursor.execute("SET CONSTRAINTS ALL DEFERRED")


def test_every_tenant_table_is_in_the_trail(db):
    tables = {
        m._meta.db_table for m in apps.get_models()
        if m.__module__.startswith("apps.") and m._meta.app_label not in ("accounts", "audit", "regions")
    }
    missing = tables - set(tracked_tables()) - set(UNTRACKED)
    assert not missing, "record new tenant tables in the trail (audit 0010 track_sql) or list why not in UNTRACKED"


def test_a_change_is_recorded_with_who_and_what(auth_client, organization, user):
    response = auth_client.post(reverse("contacts:create"), {
        "kind": "customer", "name": "PT Pelanggan Baru", "payment_terms_days": "30", "document_language": "id", "is_active": "on",
    })
    assert response.status_code == 302
    contact = Contact.objects.get(organization=organization, name="PT Pelanggan Baru")
    (created,) = changes(organization, "contacts_contact", record_id=str(contact.pk))
    assert (created.operation, created.actor, created.actor_label) == (RecordChange.Operation.INSERT, user, user.email)
    assert created.changes["name"] == "PT Pelanggan Baru" and "updated_at" not in created.changes

    record = str(contact.pk)
    contact.payment_terms_days = 14
    contact.save()  # outside a request: no user named
    contact.delete()
    updated, deleted = changes(organization, "contacts_contact", record_id=record)[1:]
    assert updated.changes == {"payment_terms_days": [30, 14]} and updated.actor is None
    assert deleted.operation == RecordChange.Operation.DELETE and deleted.changes["name"] == "PT Pelanggan Baru"
    with translation.override("en"):  # the request switched to the user's language
        assert [(f.label, f.old, f.new) for f in field_changes(updated)] == [("Payment terms (days)", "30", "14")]
    assert verify_trail(organization) == []


def test_postings_and_their_lines_are_recorded(organization, account, user):
    entry = create_journal_entry(
        organization=organization, date=date(2026, 3, 1), description="Setoran modal", created_by=user,
        lines=[JournalLineInput(account("1-1100"), debit=D("100")), JournalLineInput(account("3-1000"), credit=D("100"))],
    )
    # The line's organization comes from its entry; posting then snapshots the account onto each line.
    lines = changes(organization, "ledger_journalline")
    assert [c.operation for c in lines] == ["I", "I", "U", "U"] and set(lines[2].changes) == {"account_code", "account_name"}
    posted = changes(organization, "ledger_journalentry", record_id=str(entry.pk), operation="U")[-1]
    assert posted.changes["status"] == ["draft", "posted"] and posted.changes["entry_hash"][1] == entry.entry_hash
    # One business action is one transaction in the trail.
    assert len({c.transaction_id for c in changes(organization, "ledger_journalentry") + changes(organization, "ledger_journalline")}) == 1


def test_secrets_and_bookkeeping_columns_are_left_out(organization, user):
    key, _secret = create_key(organization=organization, user=user, name="Toko online")
    (created,) = changes(organization, "api_apikey")
    assert "key_hash" not in created.changes and created.changes["name"] == "Toko online"
    key.last_used_at = key.created_at
    key.save()
    assert len(changes(organization, "api_apikey")) == 1  # using a key isn't a change worth recording


def test_api_changes_name_the_key(client, organization, user):
    _key, secret = create_key(organization=organization, user=user, name="Toko online")
    response = client.post(reverse("api:contacts"), json.dumps({"name": "PT Pembeli"}), content_type="application/json",
                           HTTP_AUTHORIZATION=f"Bearer {secret}")
    assert response.status_code == 201
    created = changes(organization, "contacts_contact")[-1]
    assert created.actor == user and "(API key " in created.actor_label
    with connection.cursor() as cursor:  # and the next request doesn't inherit it
        cursor.execute("SELECT current_setting('audit.actor', true)")
        assert cursor.fetchone()[0] == ""


def test_commands_name_themselves():
    with mock.patch("sys.argv", ["manage.py", "process_billing"]):
        assert context.default_label() == "command:process_billing"
    with mock.patch("sys.argv", ["manage.py", "runserver"]):
        assert context.default_label() == ""


def test_the_trail_is_append_only(organization, customer):
    change = changes(organization, "contacts_contact")[0]
    with pytest.raises(IntegrityError, match="append-only"), transaction.atomic():
        RecordChange.objects.filter(pk=change.pk).update(actor_label="someone else")
    with pytest.raises(IntegrityError, match="append-only"), transaction.atomic():
        RecordChange.objects.filter(pk=change.pk).delete()


def test_tampering_breaks_the_chain(organization, customer, capsys):
    customer.name = "PT Diubah"
    customer.save()
    sequence, change_hash = trail_head(organization)
    call_command("verify_audit_trail", "--organization", str(organization.pk), "--expect-head", f"{sequence}:{change_hash}")
    first = changes(organization, "contacts_contact")[0]
    with guard_disabled() as cursor:
        cursor.execute("UPDATE audit_recordchange SET changes = %s WHERE id = %s", [json.dumps({"name": "PT Lain"}), first.pk])
        cursor.execute("DELETE FROM audit_recordchange WHERE organization_id = %s AND sequence = %s", [organization.pk, sequence])
    assert {p.problem for p in verify_trail(organization)} == {"change altered after it was recorded"}
    with pytest.raises(CommandError, match="verification failed"):
        call_command("verify_audit_trail", "--organization", str(organization.pk), "--expect-head", f"{sequence}:{change_hash}")
    assert "of the recorded head is missing" in capsys.readouterr().err  # the removed last change is caught by the head noted earlier


def test_deleting_an_organization_removes_its_trail_only(organization, other_organization, customer):
    Contact.objects.create(organization=other_organization, name="PT Lain")
    tenants.delete_organization(organization=organization)
    assert not RecordChange.objects.filter(organization_id=organization.pk).exists()
    assert RecordChange.objects.filter(organization=other_organization).exists()
    assert verify_trail(other_organization) == []


def test_the_change_history_page(auth_client, organization, customer, make_member, client):
    page = auth_client.get(reverse("audit:changes"), {"table": "contacts_contact", "record": str(customer.pk)})
    assert page.status_code == 200
    ((change, label, fields),) = page.context["rows"]
    assert (change.table_name, change.record_id) == ("contacts_contact", str(customer.pk))
    assert "PT Pelanggan" in [f.new for f in fields] and label
    link = f'{reverse("audit:changes")}?table=contacts_contact&amp;record={customer.pk}'.encode()
    assert link in auth_client.get(reverse("contacts:update", args=[customer.pk])).content
    assert auth_client.get(reverse("audit:changes"), {"table": "no_such_table"}).context["table"] == ""
    viewer = make_member(Role.VIEWER)
    client.force_login(viewer)
    assert client.get(reverse("audit:changes")).status_code == 403


def test_document_numbers_only_count_up(organization):
    with transaction.atomic():
        next_document_number(organization, "INV", date(2026, 3, 1))
        next_document_number(organization, "INV", date(2026, 3, 2))
    sequence = DocumentSequence.objects.get(organization=organization, prefix="INV")
    for change in ({"last_number": 1}, {"last_number": 5}):
        with pytest.raises(IntegrityError, match="count up"), transaction.atomic():
            DocumentSequence.objects.filter(pk=sequence.pk).update(**change)
    with pytest.raises(IntegrityError, match="never released"), transaction.atomic():
        DocumentSequence.objects.filter(pk=sequence.pk).delete()


def test_a_previous_employers_income_is_fixed_once_the_year_is_settled(organization):
    employee = Employee.objects.create(
        organization=organization, name="Budi", nik="3174061502560011", ptkp_status="TK/0", start_date=date(2025, 1, 1),
        base_salary=D("0"), fixed_allowance=D("0"), bpjs_employment=False, bpjs_health=False,
    )
    prior = payroll.add_prior_income(employee=employee, year=2026, employer_name="PT Lama", net_income=D("10000000"), pph21=D("0"),
                                     first_month=1, last_month=2)
    payroll.run_payroll(organization=organization, year=2026, month=12, pays=[PayInput(employee, D("10000000"))])
    with pytest.raises(IntegrityError, match="settled"), transaction.atomic():
        PriorIncome.objects.filter(pk=prior.pk).update(net_income=D("1"))
    with pytest.raises(IntegrityError, match="settled"), transaction.atomic():
        PriorIncome.objects.filter(pk=prior.pk).delete()


def test_coretax_submissions_are_kept_as_sent(organization):
    submission = CoretaxSubmission.objects.create(organization=organization, document_type="bpmp", label="03/2026", connector="fake",
                                                  reference="R-1")
    submission.records = [["payslip", 1]]
    submission.save()
    submission.status, submission.results = SubmissionStatus.ACCEPTED, [{"reference": "R-1", "number": "1", "error": ""}]
    submission.save()
    for change in ({"records": []}, {"status": SubmissionStatus.REJECTED}, {"reference": "R-2"}):
        with pytest.raises(IntegrityError, match="keeps what was sent"), transaction.atomic():
            CoretaxSubmission.objects.filter(pk=submission.pk).update(**change)
    with pytest.raises(IntegrityError, match="kept as sent"), transaction.atomic():
        CoretaxSubmission.objects.filter(pk=submission.pk).delete()


def test_posted_entries_stay_immutable_and_the_trail_says_so(organization, account):
    entry = create_journal_entry(
        organization=organization, date=date(2026, 3, 1), description="Setoran modal",
        lines=[JournalLineInput(account("1-1100"), debit=D("100")), JournalLineInput(account("3-1000"), credit=D("100"))],
    )
    before = RecordChange.objects.filter(organization=organization).count()
    with pytest.raises(IntegrityError), transaction.atomic():
        JournalEntry.objects.filter(pk=entry.pk).update(description="Diubah")
    assert RecordChange.objects.filter(organization=organization).count() == before  # refused changes leave no record
