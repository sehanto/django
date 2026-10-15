"""The prepayments register can't rewrite history.

* A prepayment is fixed once registered: inserted active (its reclassification, if any, a posted
  journal entry of the organization on ``recorded_on``); afterwards only its description may
  change, and active -> void only while nothing is recognised and after the reclassification is
  reversed. It is never deleted.
* Recognised months are append-only: inserted with a posted journal entry of the organization
  dated the last day of their month, inside the term of an active prepayment, never adding up to
  more than its amount; never changed or deleted.

Both are recorded in the audit trail. Only ``ledger_purge_organization`` (schema owner) lifts
this; it removes the register before the journal entries it points to.
"""

import importlib

from django.db import migrations

TRAIL = importlib.import_module("apps.audit.migrations.0010_audit_trail")
WRITE_OFFS = importlib.import_module("apps.invoicing.migrations.0028_receivable_write_offs")

GUARD_SQL = r"""
CREATE FUNCTION deferrals_guard_deferral() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    editable text[] := ARRAY['description', 'updated_at', 'created_by_id'];
BEGIN
    IF ledger_purge_allowed() THEN
        RETURN coalesce(NEW, OLD);
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'Prepayment % cannot be deleted; void it instead.', OLD.number USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'active' OR (NEW.opening_entry_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM ledger_journalentry j
             WHERE j.id = NEW.opening_entry_id AND j.organization_id = NEW.organization_id AND j.status = 'posted' AND j.date = NEW.recorded_on
        )) THEN
            RAISE EXCEPTION 'A prepayment is registered active, its reclassification posted on the date it was recorded.'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.created_by_id IS DISTINCT FROM OLD.created_by_id AND NEW.created_by_id IS NOT NULL THEN
        RAISE EXCEPTION 'The creator of prepayment % cannot change.', OLD.number USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF (to_jsonb(NEW) - editable - 'status') IS DISTINCT FROM (to_jsonb(OLD) - editable - 'status') THEN
        RAISE EXCEPTION 'Prepayment % is fixed once registered; only its description can change.', OLD.number
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.status IS DISTINCT FROM OLD.status AND (
        OLD.status <> 'active' OR NEW.status <> 'void'
        OR EXISTS (SELECT 1 FROM deferrals_deferralposting p WHERE p.deferral_id = OLD.id)
        OR (OLD.opening_entry_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM ledger_journalentry r WHERE r.reversal_of_id = OLD.opening_entry_id AND r.status = 'posted'))
    ) THEN
        RAISE EXCEPTION 'Prepayment % can only be voided before any month is recognised, after its reclassification is reversed.', OLD.number
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER deferrals_deferral_guard BEFORE INSERT OR UPDATE OR DELETE ON deferrals_deferral
    FOR EACH ROW EXECUTE FUNCTION deferrals_guard_deferral();

CREATE FUNCTION deferrals_guard_posting() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF ledger_purge_allowed() THEN
        RETURN coalesce(NEW, OLD);
    END IF;
    IF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION 'A recognised month cannot be changed or deleted.' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM deferrals_deferral d JOIN ledger_journalentry j ON j.id = NEW.journal_entry_id
         WHERE d.id = NEW.deferral_id AND d.organization_id = NEW.organization_id AND d.status = 'active'
           AND j.organization_id = NEW.organization_id AND j.status = 'posted'
           AND j.date = (make_date(NEW.year, NEW.month, 1) + interval '1 month - 1 day')::date
           AND make_date(NEW.year, NEW.month, 1) >= d.starts_on
           AND make_date(NEW.year, NEW.month, 1) < (d.starts_on + make_interval(months => d.months))::date
           AND NEW.amount + (SELECT coalesce(sum(p.amount), 0) FROM deferrals_deferralposting p WHERE p.deferral_id = d.id) <= d.amount
    ) THEN
        RAISE EXCEPTION 'A month is recognised inside the term of an active prepayment, up to its amount, with a posted journal entry on the month''s last day.'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER deferrals_deferralposting_guard BEFORE INSERT OR UPDATE OR DELETE ON deferrals_deferralposting
    FOR EACH ROW EXECUTE FUNCTION deferrals_guard_posting();
""" + TRAIL.track_sql("deferrals_deferral", "organization_id") + TRAIL.track_sql("deferrals_deferralposting", "organization_id")

DROP_SQL = TRAIL.untrack_sql("deferrals_deferralposting") + TRAIL.untrack_sql("deferrals_deferral") + """
DROP TRIGGER IF EXISTS deferrals_deferralposting_guard ON deferrals_deferralposting;
DROP FUNCTION IF EXISTS deferrals_guard_posting();
DROP TRIGGER IF EXISTS deferrals_deferral_guard ON deferrals_deferral;
DROP FUNCTION IF EXISTS deferrals_guard_deferral();
"""


def _purge_function(with_register: bool) -> str:
    """The invoicing 0028 purge function, optionally also deleting the register (before the journal entries)."""
    sql = WRITE_OFFS._purge_function(with_write_offs=True)
    if with_register:
        first = TRAIL.PREVIOUS.FIRST
        assert sql.count(first) == 1
        sql = sql.replace(first, first + (
            "    DELETE FROM deferrals_deferralposting WHERE organization_id = org_id;\n"
            "    DELETE FROM deferrals_deferral WHERE organization_id = org_id;\n"
        ))
    return sql


class Migration(migrations.Migration):

    dependencies = [
        ('deferrals', '0001_initial'),
        ('audit', '0010_audit_trail'),
        ('invoicing', '0028_receivable_write_offs'),
    ]

    operations = [
        migrations.RunSQL(GUARD_SQL, DROP_SQL),
        migrations.RunSQL(_purge_function(with_register=True), _purge_function(with_register=False)),
    ]
