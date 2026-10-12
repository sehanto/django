"""Only the table owner may lift the ledger guard; the app purges through a definer function.

0004 let any role lift the guard with ``SET ledger.purge = 'on'``, so the
data-only app role could have deleted posted lines. Now the setting counts only
for the owner of ``ledger_journalentry``, which can disable triggers anyway.
The app role offboards a tenant through ``ledger_purge_organization(org_id)``,
a SECURITY DEFINER function owned by the owner. It can only wipe an entire
organization's ledger, never edit or remove individual posted entries.
"""

from django.db import migrations

PURGE_SQL = r"""
CREATE OR REPLACE FUNCTION ledger_purge_allowed() RETURNS boolean LANGUAGE sql STABLE AS $$
    SELECT coalesce(current_setting('ledger.purge', true), '') = 'on'
       AND pg_has_role(current_user,
                       (SELECT relowner FROM pg_class WHERE oid = 'ledger_journalentry'::regclass),
                       'USAGE')
$$;

CREATE FUNCTION ledger_purge_organization(org_id bigint) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
BEGIN
    PERFORM set_config('ledger.purge', 'on', true);
    DELETE FROM ledger_journalline
     WHERE entry_id IN (SELECT id FROM ledger_journalentry WHERE organization_id = org_id);
    DELETE FROM ledger_journalentry WHERE organization_id = org_id AND reversal_of_id IS NOT NULL;
    DELETE FROM ledger_journalentry WHERE organization_id = org_id;
    PERFORM set_config('ledger.purge', 'off', true);
END
$$;

REVOKE ALL ON FUNCTION ledger_purge_organization(bigint) FROM PUBLIC;
"""

REVERSE_SQL = r"""
DROP FUNCTION IF EXISTS ledger_purge_organization(bigint);
CREATE OR REPLACE FUNCTION ledger_purge_allowed() RETURNS boolean LANGUAGE sql STABLE AS $$
    SELECT coalesce(current_setting('ledger.purge', true), '') = 'on'
$$;
"""


class Migration(migrations.Migration):

    dependencies = [
        ('ledger', '0004_immutable_ledger'),
    ]

    operations = [
        migrations.RunSQL(PURGE_SQL, REVERSE_SQL),
    ]
