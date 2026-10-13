"""PPh Final payments are immutable in the database: inserted as posted with a posted
journal entry; the only change is posted -> void once that entry is reversed; never
deleted (except by the schema owner's ``ledger_purge_organization``)."""

from django.db import migrations

GUARD_SQL = r"""
CREATE FUNCTION taxes_guard_pph_final_payment() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF ledger_purge_allowed() THEN
        RETURN coalesce(NEW, OLD);
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'PPh Final payment %/% cannot be deleted; cancel it instead.', OLD.month, OLD.year
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'posted' OR NEW.tax_amount <= 0 OR NOT EXISTS (
            SELECT 1 FROM ledger_journalentry j
             WHERE j.id = NEW.journal_entry_id AND j.status = 'posted' AND j.organization_id = NEW.organization_id
        ) THEN
            RAISE EXCEPTION 'A PPh Final payment is recorded with a posted journal entry.' USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        RETURN NEW;
    END IF;
    IF (to_jsonb(NEW) - 'status' - 'updated_at' - 'created_by_id') IS DISTINCT FROM (to_jsonb(OLD) - 'status' - 'updated_at' - 'created_by_id')
       OR (NEW.created_by_id IS DISTINCT FROM OLD.created_by_id AND NEW.created_by_id IS NOT NULL)
    THEN
        RAISE EXCEPTION 'PPh Final payment %/% cannot be changed; cancel it instead.', OLD.month, OLD.year
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.status IS DISTINCT FROM OLD.status AND (
        OLD.status <> 'posted' OR NEW.status <> 'void'
        OR NOT EXISTS (SELECT 1 FROM ledger_journalentry r WHERE r.reversal_of_id = OLD.journal_entry_id AND r.status = 'posted')
    ) THEN
        RAISE EXCEPTION 'A PPh Final payment can only be cancelled, after its journal entry is reversed.'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER taxes_pphfinalpayment_guard BEFORE INSERT OR UPDATE OR DELETE ON taxes_pphfinalpayment
    FOR EACH ROW EXECUTE FUNCTION taxes_guard_pph_final_payment();
"""

DROP_SQL = """
DROP TRIGGER IF EXISTS taxes_pphfinalpayment_guard ON taxes_pphfinalpayment;
DROP FUNCTION IF EXISTS taxes_guard_pph_final_payment();
"""


class Migration(migrations.Migration):

    dependencies = [
        ('taxes', '0005_pph_final_void'),
        ('ledger', '0006_hash_chain'),  # ledger_purge_allowed()
    ]

    operations = [
        migrations.RunSQL(GUARD_SQL, DROP_SQL),
    ]
