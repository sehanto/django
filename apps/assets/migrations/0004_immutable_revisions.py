"""Changes in estimate are append-only and prospective.

A revision is inserted for a depreciable asset in use, from a month on or after its
acquisition, after every posted month and the opening-balance months, and not before an
earlier revision. It is never changed or deleted (except by ``ledger_purge_organization``,
which now removes revisions before the assets)."""

import importlib

from django.db import migrations

GUARD_SQL = r"""
CREATE FUNCTION assets_guard_asset_revision() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF ledger_purge_allowed() THEN
        RETURN coalesce(NEW, OLD);
    END IF;
    IF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION 'A change in estimate cannot be changed or deleted; record another one.'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.effective_from <> date_trunc('month', NEW.effective_from)::date OR NOT EXISTS (
        SELECT 1 FROM assets_fixedasset a
         WHERE a.id = NEW.asset_id AND a.organization_id = NEW.organization_id AND a.status = 'active' AND a.is_depreciable
           AND NEW.effective_from >= date_trunc('month', a.acquisition_date)::date
           AND (a.depreciated_before_until IS NULL OR NEW.effective_from > a.depreciated_before_until)
    ) OR EXISTS (
        SELECT 1 FROM assets_depreciationposting p
         WHERE p.asset_id = NEW.asset_id AND make_date(p.year, p.month, 1) >= NEW.effective_from
    ) OR EXISTS (
        SELECT 1 FROM assets_assetrevision r WHERE r.asset_id = NEW.asset_id AND r.effective_from > NEW.effective_from
    ) THEN
        RAISE EXCEPTION 'A change in estimate applies from the first of a month after the depreciation already posted.'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER assets_assetrevision_guard BEFORE INSERT OR UPDATE OR DELETE ON assets_assetrevision
    FOR EACH ROW EXECUTE FUNCTION assets_guard_asset_revision();
"""

DROP_SQL = """
DROP TRIGGER IF EXISTS assets_assetrevision_guard ON assets_assetrevision;
DROP FUNCTION IF EXISTS assets_guard_asset_revision();
"""

ASSETS = "    DELETE FROM assets_fixedasset WHERE organization_id = org_id;\n"


def _purge_function(with_revisions: bool) -> str:
    """The assets 0002 purge function, optionally also deleting revisions (before the assets)."""
    sql = importlib.import_module("apps.assets.migrations.0002_immutable_register")._purge_function(with_register=True)
    assert ASSETS in sql
    if with_revisions:
        sql = sql.replace(ASSETS, "    DELETE FROM assets_assetrevision WHERE organization_id = org_id;\n" + ASSETS)
    return sql


class Migration(migrations.Migration):

    dependencies = [
        ('assets', '0003_asset_revisions'),
    ]

    operations = [
        migrations.RunSQL(GUARD_SQL, DROP_SQL),
        migrations.RunSQL(_purge_function(with_revisions=True), _purge_function(with_revisions=False)),
    ]
