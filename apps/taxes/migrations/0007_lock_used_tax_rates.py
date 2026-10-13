"""A tax rate used on an issued invoice is locked: its name, rate and DPP can't change
(issued invoices show and were computed with them). Activating or deactivating still
works; to charge a different rate, add a new one. It can't be deleted (the invoice lines'
foreign key already prevents that)."""

from django.db import migrations

GUARD_SQL = r"""
CREATE FUNCTION taxes_guard_tax_rate() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF (to_jsonb(NEW) - 'is_active' - 'updated_at') IS DISTINCT FROM (to_jsonb(OLD) - 'is_active' - 'updated_at')
       AND EXISTS (
           SELECT 1 FROM invoicing_invoiceline l JOIN invoicing_invoice i ON i.id = l.invoice_id
            WHERE l.tax_rate_id = OLD.id AND i.status <> 'draft'
       )
    THEN
        RAISE EXCEPTION 'Tax rate % is used on issued invoices; deactivate it and add a new rate instead.', OLD.name
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER taxes_taxrate_guard BEFORE UPDATE ON taxes_taxrate
    FOR EACH ROW EXECUTE FUNCTION taxes_guard_tax_rate();
"""

DROP_SQL = """
DROP TRIGGER IF EXISTS taxes_taxrate_guard ON taxes_taxrate;
DROP FUNCTION IF EXISTS taxes_guard_tax_rate();
"""


class Migration(migrations.Migration):

    dependencies = [
        ('taxes', '0006_immutable_pph_final_payments'),
        ('invoicing', '0005_party_snapshots'),
    ]

    operations = [
        migrations.RunSQL(GUARD_SQL, DROP_SQL),
    ]
