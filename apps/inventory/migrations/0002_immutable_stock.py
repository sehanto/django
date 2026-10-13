"""The stock card can't rewrite history.

* Stock movements are append-only: inserted with a posted journal entry of the same
  organization, for a tracked item of that organization; never changed or deleted.
* Once an item has movements, whether and how it is tracked (``Item.FIXED_ONCE_STOCKED``)
  is fixed.

Only ``ledger_purge_organization`` (schema owner) lifts this; it now removes the stock
movements first, before the invoice lines and journal entries they point to.
"""

import importlib

from django.db import migrations

GUARD_SQL = r"""
CREATE FUNCTION inventory_guard_stock_movement() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF ledger_purge_allowed() THEN
        RETURN coalesce(NEW, OLD);
    END IF;
    IF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION 'Stock movements cannot be changed or deleted.' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM invoicing_item i JOIN ledger_journalentry j ON j.id = NEW.journal_entry_id
         WHERE i.id = NEW.item_id AND i.organization_id = NEW.organization_id AND i.track_inventory
           AND j.organization_id = NEW.organization_id AND j.status = 'posted'
    ) THEN
        RAISE EXCEPTION 'A stock movement is posted for a tracked item with a posted journal entry.'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER inventory_stockmovement_guard BEFORE INSERT OR UPDATE OR DELETE ON inventory_stockmovement
    FOR EACH ROW EXECUTE FUNCTION inventory_guard_stock_movement();

CREATE FUNCTION inventory_guard_stocked_item() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF ledger_purge_allowed() THEN
        RETURN NEW;
    END IF;
    IF (NEW.type, NEW.track_inventory, NEW.inventory_account_id, NEW.cogs_account_id)
       IS DISTINCT FROM (OLD.type, OLD.track_inventory, OLD.inventory_account_id, OLD.cogs_account_id)
       AND EXISTS (SELECT 1 FROM inventory_stockmovement m WHERE m.item_id = OLD.id) THEN
        RAISE EXCEPTION 'Item % has stock movements; whether and how it is tracked cannot change.', OLD.name
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER inventory_item_stock_guard BEFORE UPDATE ON invoicing_item
    FOR EACH ROW EXECUTE FUNCTION inventory_guard_stocked_item();
"""

DROP_SQL = """
DROP TRIGGER IF EXISTS inventory_item_stock_guard ON invoicing_item;
DROP FUNCTION IF EXISTS inventory_guard_stocked_item();
DROP TRIGGER IF EXISTS inventory_stockmovement_guard ON inventory_stockmovement;
DROP FUNCTION IF EXISTS inventory_guard_stock_movement();
"""

FIRST = "    PERFORM set_config('ledger.purge', 'on', true);\n"


def _purge_function(with_stock: bool) -> str:
    """The assets 0004 purge function, optionally deleting stock movements first."""
    sql = importlib.import_module("apps.assets.migrations.0004_immutable_revisions")._purge_function(with_revisions=True)
    assert FIRST in sql
    if with_stock:
        sql = sql.replace(FIRST, FIRST + "    DELETE FROM inventory_stockmovement WHERE organization_id = org_id;\n", 1)
    return sql


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0001_initial'),
        ('assets', '0005_revision_method'),
    ]

    operations = [
        migrations.RunSQL(GUARD_SQL, DROP_SQL),
        migrations.RunSQL(_purge_function(with_stock=True), _purge_function(with_stock=False)),
    ]
