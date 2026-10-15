"""Signed checkpoints of the hash chains, outside the database (apps/audit/checkpoints.py): a
chain rewritten and recomputed in the database no longer leads to the head checkpointed before,
and the checkpoint file can't be edited, forged or cut in the middle unnoticed."""

import json
from datetime import date, timedelta

import pytest
from django.core.management import CommandError, call_command
from django.urls import reverse

from apps.audit.checkpoints import Chain, CheckpointLog
from apps.audit.domain import AuditAction
from apps.audit.models import AuditEvent
from apps.ledger.selectors import verify_chain
from apps.ledger.tests.test_hash_chain import entry, triggers_disabled
from apps.tenants.roles import Role


@pytest.fixture
def log(tmp_path, settings):
    settings.CHAIN_CHECKPOINT_FILE = str(tmp_path / "checkpoints" / "chains.jsonl")
    return CheckpointLog(tmp_path / "checkpoints" / "chains.jsonl")


def lines(log):
    return log.path.read_text().splitlines()


def test_each_clean_run_appends_a_signed_checkpoint_per_organization(log, organization, other_organization, account):
    entry(organization, account)
    call_command("verify_ledger")
    call_command("verify_ledger")
    checkpoints, problems = log.read()
    assert problems == [] and len(checkpoints) == 2  # the other organization has nothing posted yet
    latest, _ = log.latest(Chain.LEDGER)
    assert latest[organization.pk].sequence == 1 and set(latest) == {organization.pk}
    call_command("verify_audit_trail")
    trail, _ = log.latest(Chain.AUDIT_TRAIL)
    assert set(trail) == {organization.pk, other_organization.pk}


def test_a_chain_rewritten_after_its_checkpoint_is_caught(log, organization, account, capsys):
    entry(organization, account)
    entry(organization, account)
    call_command("verify_ledger")
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
    assert verify_chain(organization) == []  # internally consistent again...
    with pytest.raises(CommandError, match="1 problem"):
        call_command("verify_ledger")
    assert "checkpoint of" in capsys.readouterr().err  # ...but not the chain checkpointed before
    assert len(lines(log)) == 1  # nothing is checkpointed over a failure


def test_a_rewritten_audit_trail_is_caught(log, organization, capsys):
    call_command("verify_audit_trail")
    # As if the trail at this point used to read otherwise (the checkpoint is genuine, signed with the key).
    (sequence, _), = [(c.sequence, c.hash) for c in log.read()[0] if c.organization == organization.pk]
    log.append(Chain.AUDIT_TRAIL, {organization.pk: (sequence, "f" * 64)})
    with pytest.raises(CommandError, match="1 problem"):
        call_command("verify_audit_trail")
    assert "the trail was rewritten" in capsys.readouterr().err


def test_an_edited_forged_or_removed_line_is_caught(log, organization, account, capsys):
    entry(organization, account)
    call_command("verify_ledger")
    call_command("verify_ledger")
    first, second = lines(log)

    edited = json.loads(first) | {"sequence": 7}
    log.path.write_text(json.dumps(edited) + "\n" + second + "\n")
    with pytest.raises(CommandError):
        call_command("verify_ledger")
    assert "line 1: the signature doesn't match" in capsys.readouterr().err

    log.path.write_text(second + "\n")  # the first line removed
    with pytest.raises(CommandError):
        call_command("verify_ledger")
    assert "line 1: it doesn't follow the line before" in capsys.readouterr().err

    log.path.write_text(first + "\n" + second + "\n" + "{not json\n")
    with pytest.raises(CommandError):
        call_command("verify_ledger")
    assert "line 3: not a checkpoint" in capsys.readouterr().err


def test_without_a_checkpoint_file_nothing_is_written(organization, account, settings, tmp_path):
    settings.CHAIN_CHECKPOINT_FILE = ""
    entry(organization, account)
    call_command("verify_ledger")
    assert list(tmp_path.iterdir()) == []


# --- Reopening a locked period ------------------------------------------------------------


def settings_post(organization, lock_date):
    return {"name": organization.name, "legal_form": organization.legal_form, "address": "", "phone": "", "email": "",
            "uses_pph_final": "on", "lock_date": lock_date, "inventory_cost_formula": organization.inventory_cost_formula,
            "depreciation_method": organization.depreciation_method}


@pytest.fixture
def locked(organization):
    organization.lock_date = date.today() - timedelta(days=30)
    organization.save(update_fields=["lock_date"])
    return organization.lock_date


def test_only_the_owner_reopens_a_locked_period_and_it_is_audited(client, organization, make_member, user, locked):
    client.force_login(make_member(Role.ADMIN))
    earlier = (locked - timedelta(days=10)).isoformat()
    response = client.post(reverse("tenants:settings"), settings_post(organization, earlier))
    assert response.status_code == 200 and "lock_date" in response.context["form"].errors
    client.post(reverse("tenants:settings"), settings_post(organization, ""))
    organization.refresh_from_db()
    assert organization.lock_date == locked
    client.post(reverse("tenants:settings"), settings_post(organization, (locked + timedelta(days=5)).isoformat()))
    organization.refresh_from_db()
    assert organization.lock_date == locked + timedelta(days=5)  # moving it forward closes more: anyone who manages

    client.force_login(user)  # the owner
    client.post(reverse("tenants:settings"), settings_post(organization, earlier))
    organization.refresh_from_db()
    assert organization.lock_date.isoformat() == earlier
    event = AuditEvent.objects.get(action=AuditAction.PERIOD_REOPENED)
    assert event.actor == user and event.details["lock_date"] == earlier
