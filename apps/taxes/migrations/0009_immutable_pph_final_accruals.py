"""PPh Final expenses (accruals) are immutable like the deposits (0006): inserted as posted
with a posted journal entry; the only change is posted -> void once that entry is reversed
and no recorded deposit settles it; never deleted (except by ``ledger_purge_organization``,
which now removes them after the deposits that point to them).

A deposit that settles an accrual must match its organization, period and amount.
"""

import importlib

from django.db import migrations

GUARD_SQL = r"""
CREATE FUNCTION taxes_guard_pph_final_accrual() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF ledger_purge_allowed() THEN
        RETURN coalesce(NEW, OLD);
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'PPh Final expense %/% cannot be deleted; cancel it instead.', OLD.month, OLD.year
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'posted' OR NEW.tax_amount <= 0 OR NOT EXISTS (
            SELECT 1 FROM ledger_journalentry j
             WHERE j.id = NEW.journal_entry_id AND j.status = 'posted' AND j.organization_id = NEW.organization_id
        ) THEN
            RAISE EXCEPTION 'A PPh Final expense is recorded with a posted journal entry.' USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        RETURN NEW;
    END IF;
    IF (to_jsonb(NEW) - 'status' - 'updated_at' - 'created_by_id') IS DISTINCT FROM (to_jsonb(OLD) - 'status' - 'updated_at' - 'created_by_id')
       OR (NEW.created_by_id IS DISTINCT FROM OLD.created_by_id AND NEW.created_by_id IS NOT NULL)
    THEN
        RAISE EXCEPTION 'PPh Final expense %/% cannot be changed; cancel it instead.', OLD.month, OLD.year
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.status IS DISTINCT FROM OLD.status AND (
        OLD.status <> 'posted' OR NEW.status <> 'void'
        OR NOT EXISTS (SELECT 1 FROM ledger_journalentry r WHERE r.reversal_of_id = OLD.journal_entry_id AND r.status = 'posted')
        OR EXISTS (SELECT 1 FROM taxes_pphfinalpayment p WHERE p.accrual_id = OLD.id AND p.status = 'posted')
    ) THEN
        RAISE EXCEPTION 'A PPh Final expense can only be cancelled after its journal entry is reversed and its deposit cancelled.'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER taxes_pphfinalaccrual_guard BEFORE INSERT OR UPDATE OR DELETE ON taxes_pphfinalaccrual
    FOR EACH ROW EXECUTE FUNCTION taxes_guard_pph_final_accrual();

CREATE FUNCTION taxes_check_pph_final_settlement() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.accrual_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM taxes_pphfinalaccrual a
         WHERE a.id = NEW.accrual_id AND a.status = 'posted' AND a.organization_id = NEW.organization_id
           AND a.year = NEW.year AND a.month = NEW.month AND a.tax_amount = NEW.tax_amount
    ) THEN
        RAISE EXCEPTION 'A PPh Final deposit settles a recorded expense of the same period and amount.'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER taxes_pphfinalpayment_settles BEFORE INSERT ON taxes_pphfinalpayment
    FOR EACH ROW EXECUTE FUNCTION taxes_check_pph_final_settlement();
"""

DROP_SQL = """
DROP TRIGGER IF EXISTS taxes_pphfinalpayment_settles ON taxes_pphfinalpayment;
DROP FUNCTION IF EXISTS taxes_check_pph_final_settlement();
DROP TRIGGER IF EXISTS taxes_pphfinalaccrual_guard ON taxes_pphfinalaccrual;
DROP FUNCTION IF EXISTS taxes_guard_pph_final_accrual();
"""

DEPOSITS = "    DELETE FROM taxes_pphfinalpayment WHERE organization_id = org_id;\n"


def _purge_function(with_accruals: bool) -> str:
    """The ledger 0008 purge function, optionally also deleting accruals (after the deposits)."""
    sql = importlib.import_module("apps.ledger.migrations.0008_immutable_accounts")._purge_function(with_accounts=True)
    assert DEPOSITS in sql
    if with_accruals:
        sql = sql.replace(DEPOSITS, DEPOSITS + "    DELETE FROM taxes_pphfinalaccrual WHERE organization_id = org_id;\n")
    return sql


class Migration(migrations.Migration):

    dependencies = [
        ('taxes', '0008_pph_final_accrual'),
        ('ledger', '0008_immutable_accounts'),
    ]

    operations = [
        migrations.RunSQL(GUARD_SQL, DROP_SQL),
        migrations.RunSQL(_purge_function(with_accruals=True), _purge_function(with_accruals=False)),
    ]
