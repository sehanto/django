"""Bank statement lines stay as the bank reported them.

* Inserted not reconciled; their date, amount, text, balance and account never change, and they
  are never deleted, apart from the organization's own deletion (``audit_erasing``).
* Reconciling links a line to a posted journal line of the organization on the same bank account
  with the same amount on the same side (money in: a debit; money out: a credit); undoing it
  clears the link. Ignoring needs no link.

Both are recorded in the audit trail.
"""

import importlib

from django.db import migrations

TRAIL = importlib.import_module("apps.audit.migrations.0010_audit_trail")

GUARD_SQL = r"""
CREATE FUNCTION banking_guard_statement_line() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    reconciling text[] := ARRAY['status', 'journal_line_id', 'reconciled_by_id', 'reconciled_at', 'updated_at'];
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF audit_erasing(OLD.organization_id) THEN
            RETURN OLD;
        END IF;
        RAISE EXCEPTION 'A bank statement line is kept as the bank reported it.' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'unmatched' OR NEW.journal_line_id IS NOT NULL THEN
            RAISE EXCEPTION 'A bank statement line is added before it is reconciled.' USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        RETURN NEW;
    END IF;
    IF (to_jsonb(NEW) - reconciling) IS DISTINCT FROM (to_jsonb(OLD) - reconciling) THEN
        RAISE EXCEPTION 'A bank statement line is kept as the bank reported it.' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.journal_line_id IS NOT NULL AND NEW.journal_line_id IS DISTINCT FROM OLD.journal_line_id AND NOT EXISTS (
        SELECT 1 FROM ledger_journalline l JOIN ledger_journalentry e ON e.id = l.entry_id
         WHERE l.id = NEW.journal_line_id AND e.organization_id = NEW.organization_id AND e.status = 'posted'
           AND l.account_id = NEW.account_id
           AND ((NEW.amount > 0 AND l.debit = NEW.amount AND l.credit = 0) OR (NEW.amount < 0 AND l.credit = -NEW.amount AND l.debit = 0))
    ) THEN
        RAISE EXCEPTION 'A statement line is reconciled with a posted entry of the same amount on its bank account.'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER banking_statementline_guard BEFORE INSERT OR UPDATE OR DELETE ON banking_statementline
    FOR EACH ROW EXECUTE FUNCTION banking_guard_statement_line();
""" + TRAIL.track_sql("banking_statementline", "organization_id")

DROP_SQL = TRAIL.untrack_sql("banking_statementline") + """
DROP TRIGGER IF EXISTS banking_statementline_guard ON banking_statementline;
DROP FUNCTION IF EXISTS banking_guard_statement_line();
"""


class Migration(migrations.Migration):

    dependencies = [
        ("banking", "0001_initial"),
        ("audit", "0010_audit_trail"),
    ]

    operations = [migrations.RunSQL(GUARD_SQL, DROP_SQL)]
