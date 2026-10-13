"""Append-only audit trail of the ledger: gap-free posting sequence, who posted, SHA-256 hash chain.

The chain is written by the database (ledger migration 0006). The tamper tests play
someone who bypasses the triggers (the schema owner disabling them); the test database
connection is the owner, and every change is rolled back with the test.
"""

from contextlib import contextmanager
from datetime import date
from decimal import Decimal

import pytest
from django.core.management import CommandError, call_command
from django.db import IntegrityError, connection, transaction
from django.urls import reverse

from apps.audit.domain import AuditAction
from apps.audit.models import AuditEvent
from apps.ledger import services
from apps.ledger.models import Account, JournalEntry, JournalLine
from apps.ledger.selectors import ChainHead, chain_head, head_mismatch, verify_chain
from apps.ledger.services import JournalLineInput

D = Decimal
GENESIS = "0" * 64


def entry(organization, account, amount="1000", *, post=True, user=None, on=date(2026, 1, 2)):
    return services.create_journal_entry(
        organization=organization, date=on, description="Setoran modal", created_by=user, post=post,
        lines=[JournalLineInput(account("1-1100"), debit=D(amount)), JournalLineInput(account("3-1000"), credit=D(amount))],
    )


@contextmanager
def triggers_disabled():
    """What an attacker with the owner role (or a superuser) could do."""
    with connection.cursor() as cursor:
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")  # ALTER TABLE refuses while deferred FK checks are pending
        cursor.execute("ALTER TABLE ledger_journalentry DISABLE TRIGGER ledger_journalentry_guard")
        cursor.execute("ALTER TABLE ledger_journalline DISABLE TRIGGER ledger_journalline_guard")
        try:
            yield cursor
        finally:
            cursor.execute("ALTER TABLE ledger_journalentry ENABLE TRIGGER ledger_journalentry_guard")
            cursor.execute("ALTER TABLE ledger_journalline ENABLE TRIGGER ledger_journalline_guard")
            cursor.execute("SET CONSTRAINTS ALL DEFERRED")


def problems(organization) -> list[str]:
    return [p.problem for p in verify_chain(organization)]


def test_posting_appends_to_a_gap_free_chain(organization, account, user):
    deleted_draft = entry(organization, account, post=False)  # takes JU/.../0001
    draft = entry(organization, account, post=False)
    services.delete_draft_journal_entry(entry=deleted_draft)  # the number has a gap now...
    first = services.post_journal_entry(entry=draft, posted_by=user)
    second = entry(organization, account, user=user)

    assert (first.posting_sequence, second.posting_sequence) == (1, 2)  # ...the posting sequence does not
    assert first.previous_hash == GENESIS and second.previous_hash == first.entry_hash
    assert len(second.entry_hash) == 64 and first.posted_by == second.posted_by == user
    assert problems(organization) == []
    assert chain_head(organization) == ChainHead(2, second.entry_hash)


def test_each_organization_has_its_own_chain(organization, other_organization, account):
    entry(organization, account)
    other = services.create_journal_entry(
        organization=other_organization, date=date(2026, 1, 2), description="x",
        lines=[JournalLineInput(Account.objects.get(organization=other_organization, code="1-1100"), debit=D("5")),
               JournalLineInput(Account.objects.get(organization=other_organization, code="3-1000"), credit=D("5"))],
    )
    assert other.posting_sequence == 1 and other.previous_hash == GENESIS


def test_drafts_and_the_app_cannot_write_chain_fields(organization, account):
    draft = entry(organization, account, post=False)
    JournalEntry.objects.filter(pk=draft.pk).update(posting_sequence=7, entry_hash="f" * 64)
    draft.refresh_from_db()
    assert draft.posting_sequence is None and draft.entry_hash == ""  # the trigger clears them on drafts

    posted = entry(organization, account)
    for change in ({"entry_hash": "f" * 64}, {"posting_sequence": 99}, {"previous_hash": GENESIS[:-1] + "1"}):
        with pytest.raises(IntegrityError), transaction.atomic():
            JournalEntry.objects.filter(pk=posted.pk).update(**change)


def test_a_changed_amount_is_detected(organization, account):
    first, second = entry(organization, account), entry(organization, account, "2000")
    with triggers_disabled():
        # Balanced edit, so only the hash can tell.
        JournalLine.objects.filter(entry=first, account__code="1-1100").update(debit=D("900"))
        JournalLine.objects.filter(entry=first, account__code="3-1000").update(credit=D("900"))
    assert problems(organization) == ["content changed after posting"]
    assert [p.number for p in verify_chain(organization)] == [first.number]
    assert second.posting_sequence == 2


def test_a_removed_entry_is_detected(organization, account):
    first, middle, last = entry(organization, account), entry(organization, account), entry(organization, account)
    with triggers_disabled():
        JournalLine.objects.filter(entry=middle).delete()
        JournalEntry.objects.filter(pk=middle.pk).delete()
    found = verify_chain(organization)
    assert {p.problem for p in found} == {"sequence gap: expected 2 (entries missing)", "previous hash does not match the preceding entry"}
    assert all(p.number == last.number for p in found)
    assert first.posting_sequence == 1


def test_a_rewritten_chain_is_caught_by_a_recorded_head(organization, account):
    entry(organization, account)
    entry(organization, account)
    recorded = chain_head(organization)
    with triggers_disabled() as cursor:
        # A careful attacker changes an entry and recomputes every hash after it.
        cursor.execute("UPDATE ledger_journalentry SET description = 'diubah' WHERE organization_id = %s AND posting_sequence = 1", [organization.pk])
        cursor.execute("""
            DO $$ DECLARE e ledger_journalentry%%ROWTYPE; prev text := repeat('0', 64);
            BEGIN
              FOR e IN SELECT * FROM ledger_journalentry WHERE organization_id = %s AND posting_sequence IS NOT NULL ORDER BY posting_sequence LOOP
                UPDATE ledger_journalentry SET previous_hash = prev, entry_hash = ledger_entry_digest(e, e.posting_sequence, prev)
                 WHERE id = e.id RETURNING entry_hash INTO prev;
              END LOOP;
            END $$;""" % int(organization.pk))
    assert problems(organization) == []  # internally consistent again...
    assert "rewritten" in head_mismatch(organization, recorded)  # ...but no longer leads to the recorded head


def test_verify_ledger_command(organization, account, capsys):
    entry(organization, account)
    call_command("verify_ledger")
    assert "Ledger intact: " in capsys.readouterr().out
    head = chain_head(organization)
    call_command("verify_ledger", "--organization", str(organization.pk), "--expect-head", str(head))
    with pytest.raises(CommandError, match="--expect-head must look like"):
        call_command("verify_ledger", "--organization", str(organization.pk), "--expect-head", "1:abc")

    with triggers_disabled():
        JournalEntry.objects.filter(organization=organization, posting_sequence=head.sequence).update(reference="x")
    with pytest.raises(CommandError, match="1 problem"):
        call_command("verify_ledger")
    assert "content changed after posting" in capsys.readouterr().err


def test_chart_of_accounts_changes_are_audited(auth_client, organization, account):
    cash = account("1-1100")
    data = {"code": cash.code, "name": "Bank BCA", "category": cash.category, "description": "", "is_active": "on"}
    auth_client.post(reverse("ledger:account_update", args=[cash.pk]), data)
    changed = AuditEvent.objects.get(action=AuditAction.ACCOUNT_CHANGED, organization=organization)
    assert changed.details["changes"] == {"name": [cash.name, "Bank BCA"]}
    assert "Bank BCA" in auth_client.get(reverse("audit:organization")).content.decode()
