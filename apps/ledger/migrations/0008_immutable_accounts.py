"""The chart of accounts can't rewrite history, at the database level.

- An account with posted journal lines keeps its category, contra flag and system role:
  they decide which report the history lands in and which way it counts. Code, name and
  description stay editable (changes are recorded in the audit log), and it can still
  be (de)activated.
- A system account (receivables, payables, PPN, ...) is never deleted, deactivated or
  stripped of its role.
- No account moves to another organization.
- Accounts with any journal lines can't be deleted (the lines' foreign key).

Organization deletion removes the accounts inside the owner's
``ledger_purge_organization`` (the only place the guard is lifted).
"""

import importlib

from django.db import migrations

GUARD_SQL = r"""
CREATE FUNCTION ledger_guard_account() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF ledger_purge_allowed() THEN
        RETURN coalesce(NEW, OLD);
    END IF;
    IF TG_OP = 'DELETE' THEN
        IF OLD.system_role IS NOT NULL THEN
            RAISE EXCEPTION 'System account % cannot be deleted.', OLD.code USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        RETURN OLD;
    END IF;
    IF NEW.organization_id IS DISTINCT FROM OLD.organization_id THEN
        RAISE EXCEPTION 'Account % cannot move to another organization.', OLD.code USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF OLD.system_role IS NOT NULL AND (NEW.system_role IS DISTINCT FROM OLD.system_role OR NOT NEW.is_active) THEN
        RAISE EXCEPTION 'System account % cannot be deactivated or lose its role.', OLD.code
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF (NEW.category, NEW.is_contra, NEW.system_role) IS DISTINCT FROM (OLD.category, OLD.is_contra, OLD.system_role)
       AND EXISTS (
           SELECT 1 FROM ledger_journalline l JOIN ledger_journalentry e ON e.id = l.entry_id
            WHERE l.account_id = OLD.id AND e.status = 'posted'
       )
    THEN
        RAISE EXCEPTION 'Account % has posted transactions: its category, contra flag and system role are fixed.', OLD.code
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER ledger_account_guard BEFORE UPDATE OR DELETE ON ledger_account
    FOR EACH ROW EXECUTE FUNCTION ledger_guard_account();
"""

DROP_SQL = """
DROP TRIGGER IF EXISTS ledger_account_guard ON ledger_account;
DROP FUNCTION IF EXISTS ledger_guard_account();
"""

DOCUMENTS = "    DELETE FROM invoicing_invoice WHERE organization_id = org_id;\n"


def _purge_function(with_accounts: bool) -> str:
    """The 0007 purge function, optionally also deleting the organization's accounts (last)."""
    sql = importlib.import_module("apps.ledger.migrations.0007_purge_documents").PURGE_SQL
    end_marker = "    DELETE FROM ledger_journalentry WHERE organization_id = org_id;\n"
    assert DOCUMENTS in sql and end_marker in sql
    if with_accounts:
        sql = sql.replace(end_marker, end_marker + "    DELETE FROM ledger_account WHERE organization_id = org_id;\n", 1)
    return sql


class Migration(migrations.Migration):

    dependencies = [
        ('ledger', '0007_purge_documents'),
    ]

    operations = [
        migrations.RunSQL(GUARD_SQL, DROP_SQL),
        migrations.RunSQL(_purge_function(with_accounts=True), _purge_function(with_accounts=False)),
    ]
