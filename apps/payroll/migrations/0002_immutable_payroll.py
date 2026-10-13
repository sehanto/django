"""Payroll runs and payslips can't rewrite history.

* A run is inserted as posted with a posted journal entry of the same organization. Afterwards
  only: paying the salaries and the BPJS (each date and entry set once, with a posted entry),
  and voiding (posted -> void, after its journal entry is reversed). Never deleted.
* Payslips are inserted with their posted run; only their bukti potong number may be set,
  once. Never deleted.

``ledger_purge_organization`` (owner only) removes them first.
"""

import importlib

from django.db import migrations

GUARD_SQL = r"""
CREATE FUNCTION payroll_guard_run() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    payment text[] := ARRAY['paid_on', 'payment_entry_id', 'bpjs_paid_on', 'bpjs_entry_id', 'status', 'updated_at', 'created_by_id'];
BEGIN
    IF ledger_purge_allowed() THEN
        RETURN coalesce(NEW, OLD);
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'Payroll %/% cannot be deleted; void it instead.', OLD.month, OLD.year USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'posted' OR NEW.payment_entry_id IS NOT NULL OR NEW.bpjs_entry_id IS NOT NULL OR NOT EXISTS (
            SELECT 1 FROM ledger_journalentry j WHERE j.id = NEW.journal_entry_id AND j.status = 'posted' AND j.organization_id = NEW.organization_id
        ) THEN
            RAISE EXCEPTION 'A payroll is recorded with a posted journal entry, unpaid.' USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        RETURN NEW;
    END IF;
    IF (to_jsonb(NEW) - payment) IS DISTINCT FROM (to_jsonb(OLD) - payment)
       OR (NEW.created_by_id IS DISTINCT FROM OLD.created_by_id AND NEW.created_by_id IS NOT NULL) THEN
        RAISE EXCEPTION 'Payroll %/% cannot be changed.', OLD.month, OLD.year USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF (NEW.payment_entry_id, NEW.paid_on) IS DISTINCT FROM (OLD.payment_entry_id, OLD.paid_on) AND (
        OLD.payment_entry_id IS NOT NULL OR NEW.paid_on IS NULL OR OLD.status <> 'posted'
        OR NOT EXISTS (SELECT 1 FROM ledger_journalentry j WHERE j.id = NEW.payment_entry_id AND j.status = 'posted' AND j.organization_id = NEW.organization_id)
    ) THEN
        RAISE EXCEPTION 'The salaries of payroll %/% are paid once, with a posted entry.', OLD.month, OLD.year USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF (NEW.bpjs_entry_id, NEW.bpjs_paid_on) IS DISTINCT FROM (OLD.bpjs_entry_id, OLD.bpjs_paid_on) AND (
        OLD.bpjs_entry_id IS NOT NULL OR NEW.bpjs_paid_on IS NULL OR OLD.status <> 'posted'
        OR NOT EXISTS (SELECT 1 FROM ledger_journalentry j WHERE j.id = NEW.bpjs_entry_id AND j.status = 'posted' AND j.organization_id = NEW.organization_id)
    ) THEN
        RAISE EXCEPTION 'The BPJS of payroll %/% is paid once, with a posted entry.', OLD.month, OLD.year USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.status IS DISTINCT FROM OLD.status AND (
        OLD.status <> 'posted' OR NEW.status <> 'void' OR OLD.payment_entry_id IS NOT NULL OR OLD.bpjs_entry_id IS NOT NULL
        OR NOT EXISTS (SELECT 1 FROM ledger_journalentry r WHERE r.reversal_of_id = OLD.journal_entry_id AND r.status = 'posted')
    ) THEN
        RAISE EXCEPTION 'Payroll %/% can only be voided while unpaid, after its journal entry is reversed.', OLD.month, OLD.year
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER payroll_payrollrun_guard BEFORE INSERT OR UPDATE OR DELETE ON payroll_payrollrun
    FOR EACH ROW EXECUTE FUNCTION payroll_guard_run();

CREATE FUNCTION payroll_guard_payslip() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF ledger_purge_allowed() THEN
        RETURN coalesce(NEW, OLD);
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'Payslips cannot be deleted.' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF TG_OP = 'INSERT' THEN
        IF NOT EXISTS (SELECT 1 FROM payroll_payrollrun r WHERE r.id = NEW.run_id AND r.status = 'posted') THEN
            RAISE EXCEPTION 'A payslip belongs to a recorded payroll.' USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        RETURN NEW;
    END IF;
    IF (to_jsonb(NEW) - 'withholding_slip' - 'updated_at') IS DISTINCT FROM (to_jsonb(OLD) - 'withholding_slip' - 'updated_at')
       OR (NEW.withholding_slip IS DISTINCT FROM OLD.withholding_slip AND (OLD.withholding_slip <> '' OR NEW.withholding_slip = '')) THEN
        RAISE EXCEPTION 'A payslip only takes its bukti potong number, once.' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER payroll_payslip_guard BEFORE INSERT OR UPDATE OR DELETE ON payroll_payslip
    FOR EACH ROW EXECUTE FUNCTION payroll_guard_payslip();
"""

DROP_SQL = """
DROP TRIGGER IF EXISTS payroll_payslip_guard ON payroll_payslip;
DROP FUNCTION IF EXISTS payroll_guard_payslip();
DROP TRIGGER IF EXISTS payroll_payrollrun_guard ON payroll_payrollrun;
DROP FUNCTION IF EXISTS payroll_guard_run();
"""

PREVIOUS = importlib.import_module("apps.taxes.migrations.0012_withholding_deposits")
FIRST = "    PERFORM set_config('ledger.purge', 'on', true);\n"


def _purge_function(with_payroll: bool) -> str:
    sql = PREVIOUS._purge_function(with_deposits=True)
    assert sql.count(FIRST) == 1
    if with_payroll:
        sql = sql.replace(FIRST, FIRST + (
            "    DELETE FROM payroll_payslip WHERE run_id IN (SELECT id FROM payroll_payrollrun WHERE organization_id = org_id);\n"
            "    DELETE FROM payroll_payrollrun WHERE organization_id = org_id;\n"
        ))
    return sql


class Migration(migrations.Migration):

    dependencies = [
        ('payroll', '0001_initial'),
        ('taxes', '0012_withholding_deposits'),
    ]

    operations = [
        migrations.RunSQL(GUARD_SQL, DROP_SQL),
        migrations.RunSQL(_purge_function(with_payroll=True), _purge_function(with_payroll=False)),
    ]
