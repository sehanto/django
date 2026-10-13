"""Organization deletion also removes the now-immutable source documents.

Invoices, invoice lines, payments and PPh Final payments are guarded like the ledger
(invoicing 0004, taxes 0006), so ``ledger_purge_organization`` (SECURITY DEFINER,
schema owner only) deletes them before the journal entries they point to.
"""

from django.db import migrations

PURGE_SQL = r"""
CREATE OR REPLACE FUNCTION ledger_purge_organization(org_id bigint) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
BEGIN
    PERFORM set_config('ledger.purge', 'on', true);
    DELETE FROM taxes_pphfinalpayment WHERE organization_id = org_id;
    DELETE FROM invoicing_payment WHERE organization_id = org_id;
    DELETE FROM invoicing_invoiceline
     WHERE invoice_id IN (SELECT id FROM invoicing_invoice WHERE organization_id = org_id);
    DELETE FROM invoicing_invoice WHERE organization_id = org_id;
    DELETE FROM ledger_journalline
     WHERE entry_id IN (SELECT id FROM ledger_journalentry WHERE organization_id = org_id);
    DELETE FROM ledger_journalentry WHERE organization_id = org_id AND reversal_of_id IS NOT NULL;
    DELETE FROM ledger_journalentry WHERE organization_id = org_id;
    PERFORM set_config('ledger.purge', 'off', true);
END
$$;
REVOKE ALL ON FUNCTION ledger_purge_organization(bigint) FROM PUBLIC;
"""

PREVIOUS_SQL = r"""
CREATE OR REPLACE FUNCTION ledger_purge_organization(org_id bigint) RETURNS void
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
"""


class Migration(migrations.Migration):

    dependencies = [
        ('ledger', '0006_hash_chain'),
        ('invoicing', '0004_immutable_documents'),
        ('taxes', '0006_immutable_pph_final_payments'),
    ]

    operations = [
        migrations.RunSQL(PURGE_SQL, PREVIOUS_SQL),
    ]
