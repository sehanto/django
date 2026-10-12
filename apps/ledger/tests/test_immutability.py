"""The database itself keeps the ledger immutable and double-entry, whoever writes to it.

These tests bypass the service layer on purpose (ORM shortcuts, querysets) to
prove the PostgreSQL guards in ``ledger/migrations/0004_immutable_ledger.py``.
"""

from contextlib import contextmanager
from datetime import date
from decimal import Decimal

import pytest
from django.contrib.admin.sites import site
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import RequestFactory
from django.utils import timezone

from apps.ledger import services
from apps.ledger.models import Account, JournalEntry, JournalLine
from apps.ledger.services import JournalLineInput

D = Decimal
ON = date(2026, 1, 2)


@contextmanager
def rejected_by_database():
    with pytest.raises(IntegrityError), transaction.atomic():
        yield


@pytest.fixture
def posted(organization, account, user):
    return services.create_journal_entry(
        organization=organization, date=ON, description="Setoran modal awal", created_by=user,
        lines=[JournalLineInput(account("1-1100"), debit=D("1000")), JournalLineInput(account("3-1000"), credit=D("1000"))],
    )


def draft_with_lines(organization, *lines, on=ON):
    entry = JournalEntry.objects.create(organization=organization, number=f"JU/T/{JournalEntry.objects.count()}", date=on, description="x")
    JournalLine.objects.bulk_create(JournalLine(entry=entry, account=a, debit=d, credit=c) for a, d, c in lines)
    return entry


def account_of(organization, code):
    return Account.objects.get(organization=organization, code=code)


def post_directly(entry):
    entry.status, entry.posted_at = JournalEntry.Status.POSTED, timezone.now()
    entry.save()


def test_posted_entry_cannot_be_edited(posted):
    posted.description = "diubah"
    with rejected_by_database():
        posted.save()
    with rejected_by_database():
        JournalEntry.objects.filter(pk=posted.pk).update(date=date(2026, 1, 3))
    with rejected_by_database():
        JournalEntry.objects.filter(pk=posted.pk).update(status=JournalEntry.Status.DRAFT, posted_at=None)


def test_posted_entry_cannot_be_deleted(posted):
    with rejected_by_database():
        posted.delete()
    with rejected_by_database():
        JournalEntry.objects.filter(pk=posted.pk).delete()
    assert JournalEntry.objects.filter(pk=posted.pk).exists()


def test_lines_of_a_posted_entry_are_frozen(posted, account):
    line = posted.lines.first()
    with rejected_by_database():
        JournalLine.objects.filter(pk=line.pk).update(debit=D("999"))
    with rejected_by_database():
        line.delete()
    with rejected_by_database():
        JournalLine.objects.create(entry=posted, account=account("1-1000"), debit=D("5"))


def test_entries_cannot_be_inserted_already_posted(organization):
    with rejected_by_database():
        JournalEntry.objects.create(
            organization=organization, number="JU/X", date=ON, description="x",
            status=JournalEntry.Status.POSTED, posted_at=timezone.now(),
        )


def test_posting_requires_balanced_lines(organization, account):
    unbalanced = draft_with_lines(organization, (account("1-1100"), D("1000"), 0), (account("3-1000"), 0, D("900")))
    with rejected_by_database():
        post_directly(unbalanced)

    single = draft_with_lines(organization, (account("1-1100"), D("1000"), 0))
    with rejected_by_database():
        post_directly(single)

    balanced = draft_with_lines(organization, (account("1-1100"), D("1000"), 0), (account("3-1000"), 0, D("1000")))
    post_directly(balanced)
    balanced.refresh_from_db()
    assert balanced.is_posted


def test_posting_rejects_other_tenants_accounts(organization, other_organization, account):
    foreign = account_of(other_organization, "3-1000")
    entry = draft_with_lines(organization, (account("1-1100"), D("1000"), 0), (foreign, 0, D("1000")))
    with rejected_by_database():
        post_directly(entry)


def test_posting_rejects_locked_period(organization, account):
    organization.lock_date = date(2026, 1, 31)
    organization.save(update_fields=["lock_date"])
    entry = draft_with_lines(organization, (account("1-1100"), D("1000"), 0), (account("3-1000"), 0, D("1000")))
    with rejected_by_database():
        post_directly(entry)


def test_posted_at_must_match_status(organization):
    with rejected_by_database():
        JournalEntry.objects.create(organization=organization, number="JU/Y", date=ON, description="x", posted_at=timezone.now())


def test_drafts_stay_editable(organization, account):
    entry = draft_with_lines(organization, (account("1-1100"), D("1000"), 0), (account("3-1000"), 0, D("1000")))
    JournalLine.objects.filter(entry=entry).update(description="ok")
    entry.description = "ok"
    entry.save()
    entry.delete()


def test_deleting_the_author_keeps_the_entry(posted, user):
    get_user_model().objects.filter(pk=user.pk).delete()
    posted.refresh_from_db()
    assert posted.created_by is None and posted.is_posted


def test_purge_lifts_the_guard_only_for_itself(posted, other_organization):
    other_entry = services.create_journal_entry(
        organization=other_organization, date=ON, description="x",
        lines=[
            JournalLineInput(account_of(other_organization, "1-1100"), debit=D("10")),
            JournalLineInput(account_of(other_organization, "3-1000"), credit=D("10")),
        ],
    )
    services.reverse_journal_entry(entry=other_entry, date=ON)

    services.purge_organization_ledger(other_organization)

    assert not JournalEntry.objects.filter(organization=other_organization).exists()
    with rejected_by_database():
        posted.delete()  # the guard is back on within the same transaction


def test_admin_cannot_delete_journal_entries(posted, user):
    request = RequestFactory().get("/")
    request.user = user
    assert site._registry[JournalEntry].has_delete_permission(request, posted) is False
