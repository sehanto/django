"""The fixed asset register can't rewrite history.

* Depreciation postings are append-only: inserted with a posted journal entry of the
  same organization dated the last day of their month, for an asset in use from that
  month on; never changed or deleted.
* An asset's figures (``FixedAsset.FIXED_ONCE_DEPRECIATED``) are fixed once depreciation
  has been posted for it. Disposal is one step, active -> disposed, with a posted
  journal entry; a disposed asset only takes a new name or description.
* Assets with postings or a disposal can't be deleted.

Only ``ledger_purge_organization`` (schema owner) lifts this; it now removes the
register before the journal entries it points to.
"""

import importlib

from django.db import migrations

GUARD_SQL = r"""
CREATE FUNCTION assets_guard_fixed_asset() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    editable text[] := ARRAY['name', 'description', 'updated_at', 'created_by_id'];
    disposal text[] := ARRAY['status', 'disposed_on', 'disposal_proceeds', 'disposal_entry_id'];
    used boolean;
BEGIN
    IF ledger_purge_allowed() THEN
        RETURN coalesce(NEW, OLD);
    END IF;
    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'active' OR NEW.disposed_on IS NOT NULL OR NEW.disposal_entry_id IS NOT NULL OR NEW.disposal_proceeds <> 0 THEN
            RAISE EXCEPTION 'A fixed asset is registered in use; disposal is a separate step.' USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        RETURN NEW;
    END IF;
    used := OLD.status = 'disposed' OR EXISTS (SELECT 1 FROM assets_depreciationposting p WHERE p.asset_id = OLD.id);
    IF TG_OP = 'DELETE' THEN
        IF used THEN
            RAISE EXCEPTION 'Fixed asset % has depreciation or a disposal and cannot be deleted.', OLD.number
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        RETURN OLD;
    END IF;
    IF NEW.created_by_id IS DISTINCT FROM OLD.created_by_id AND NEW.created_by_id IS NOT NULL THEN
        RAISE EXCEPTION 'The creator of fixed asset % cannot change.', OLD.number USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF OLD.status = 'disposed' THEN
        IF (to_jsonb(NEW) - editable) IS DISTINCT FROM (to_jsonb(OLD) - editable) THEN
            RAISE EXCEPTION 'Fixed asset % has been disposed; only its name and description can change.', OLD.number
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.status = 'disposed' THEN
        IF NEW.disposed_on IS NULL OR NEW.disposed_on < OLD.acquisition_date OR NOT EXISTS (
            SELECT 1 FROM ledger_journalentry j
             WHERE j.id = NEW.disposal_entry_id AND j.status = 'posted' AND j.organization_id = NEW.organization_id
               AND j.date = NEW.disposed_on
        ) OR (to_jsonb(NEW) - editable - disposal) IS DISTINCT FROM (to_jsonb(OLD) - editable - disposal) THEN
            RAISE EXCEPTION 'Fixed asset % is disposed with a posted journal entry on the disposal date, and nothing else changes.', OLD.number
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.status <> 'active' OR (to_jsonb(NEW) -> 'disposed_on', to_jsonb(NEW) -> 'disposal_proceeds', to_jsonb(NEW) -> 'disposal_entry_id')
       IS DISTINCT FROM (to_jsonb(OLD) -> 'disposed_on', to_jsonb(OLD) -> 'disposal_proceeds', to_jsonb(OLD) -> 'disposal_entry_id') THEN
        RAISE EXCEPTION 'Fixed asset % can only be disposed, with its journal entry.', OLD.number USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF used AND (to_jsonb(NEW) - editable) IS DISTINCT FROM (to_jsonb(OLD) - editable) THEN
        RAISE EXCEPTION 'Fixed asset % has posted depreciation; its figures cannot change.', OLD.number
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER assets_fixedasset_guard BEFORE INSERT OR UPDATE OR DELETE ON assets_fixedasset
    FOR EACH ROW EXECUTE FUNCTION assets_guard_fixed_asset();

CREATE FUNCTION assets_guard_depreciation_posting() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF ledger_purge_allowed() THEN
        RETURN coalesce(NEW, OLD);
    END IF;
    IF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION 'Posted depreciation cannot be changed or deleted.' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM assets_fixedasset a JOIN ledger_journalentry j ON j.id = NEW.journal_entry_id
         WHERE a.id = NEW.asset_id AND a.organization_id = NEW.organization_id AND a.status = 'active' AND a.is_depreciable
           AND j.organization_id = NEW.organization_id AND j.status = 'posted'
           AND j.date = (make_date(NEW.year, NEW.month, 1) + interval '1 month - 1 day')::date
           AND make_date(NEW.year, NEW.month, 1) >= date_trunc('month', a.acquisition_date)::date
    ) THEN
        RAISE EXCEPTION 'Depreciation is posted for an asset in use, with a posted journal entry on the last day of its month.'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER assets_depreciationposting_guard BEFORE INSERT OR UPDATE OR DELETE ON assets_depreciationposting
    FOR EACH ROW EXECUTE FUNCTION assets_guard_depreciation_posting();
"""

DROP_SQL = """
DROP TRIGGER IF EXISTS assets_depreciationposting_guard ON assets_depreciationposting;
DROP FUNCTION IF EXISTS assets_guard_depreciation_posting();
DROP TRIGGER IF EXISTS assets_fixedasset_guard ON assets_fixedasset;
DROP FUNCTION IF EXISTS assets_guard_fixed_asset();
"""

DEPOSITS = "    DELETE FROM taxes_pphfinalpayment WHERE organization_id = org_id;\n"


def _purge_function(with_register: bool) -> str:
    """The taxes 0009 purge function, optionally also deleting the register (first)."""
    sql = importlib.import_module("apps.taxes.migrations.0009_immutable_pph_final_accruals")._purge_function(with_accruals=True)
    assert DEPOSITS in sql
    if with_register:
        sql = sql.replace(DEPOSITS, (
            "    DELETE FROM assets_depreciationposting WHERE organization_id = org_id;\n"
            "    DELETE FROM assets_fixedasset WHERE organization_id = org_id;\n"
        ) + DEPOSITS)
    return sql


class Migration(migrations.Migration):

    dependencies = [
        ('assets', '0001_initial'),
        ('taxes', '0009_immutable_pph_final_accruals'),
    ]

    operations = [
        migrations.RunSQL(GUARD_SQL, DROP_SQL),
        migrations.RunSQL(_purge_function(with_register=True), _purge_function(with_register=False)),
    ]
