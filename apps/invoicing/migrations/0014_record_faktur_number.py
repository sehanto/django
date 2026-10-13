"""An issued invoice may take its Faktur Pajak number once, while it has none.

Coretax assigns the number after the faktur is uploaded, so it arrives after the invoice
was issued. Every other column stays as 0004-0013 left it; a number, once recorded,
doesn't change.
"""

import importlib

from django.db import migrations

FROZEN = "(to_jsonb(NEW) - 'status' - 'amount_paid' - 'amount_credited' - 'updated_at' - 'created_by_id')"
FROZEN_OLD = "(to_jsonb(OLD) - 'status' - 'amount_paid' - 'amount_credited' - 'updated_at' - 'created_by_id')"
UNCHANGED = "IF (NEW.status, NEW.amount_paid, NEW.amount_credited) IS NOT DISTINCT FROM (OLD.status, OLD.amount_paid, OLD.amount_credited) THEN"
NUMBER_CHECK = """IF NEW.tax_invoice_number IS DISTINCT FROM OLD.tax_invoice_number
       AND (OLD.tax_invoice_number <> '' OR NEW.tax_invoice_number !~ '^[0-9]{16,17}$')
    THEN
        RAISE EXCEPTION 'Invoice % keeps its Faktur Pajak number; an issued invoice takes one only while it has none.', OLD.number
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    """


def _invoice_guard(with_number: bool) -> str:
    function = importlib.import_module("apps.invoicing.migrations.0013_voidable_returns")._invoice_guard(with_voidable_returns=True)
    if with_number:
        for old, new in [
            (FROZEN, FROZEN.replace("'amount_credited'", "'amount_credited' - 'tax_invoice_number'")),
            (FROZEN_OLD, FROZEN_OLD.replace("'amount_credited'", "'amount_credited' - 'tax_invoice_number'")),
            (UNCHANGED, NUMBER_CHECK + UNCHANGED),
        ]:
            assert function.count(old) == 1, old
            function = function.replace(old, new)
    return function


class Migration(migrations.Migration):

    dependencies = [
        ('invoicing', '0013_voidable_returns'),
    ]

    operations = [
        migrations.RunSQL(_invoice_guard(with_number=True), _invoice_guard(with_number=False)),
    ]
