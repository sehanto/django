"""A fixed asset may be sold on a sales invoice (code 09 for a PKP); the asset guard (0002) lets
the link be set with the disposal only."""

import django.db.models.deletion
import importlib

from django.db import migrations, models

GUARD_SQL = importlib.import_module("apps.assets.migrations.0002_immutable_register").GUARD_SQL
PREVIOUS = GUARD_SQL[:GUARD_SQL.index("$$;") + 3].replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)
CHANGES = [
    # The invoice is set with the disposal, never before or after it.
    ("disposal text[] := ARRAY['status', 'disposed_on', 'disposal_proceeds', 'disposal_entry_id'];",
     "disposal text[] := ARRAY['status', 'disposed_on', 'disposal_proceeds', 'disposal_entry_id', 'disposal_invoice_id'];"),
    ("OR NEW.disposal_entry_id IS NOT NULL OR NEW.disposal_proceeds <> 0 THEN",
     "OR NEW.disposal_entry_id IS NOT NULL OR NEW.disposal_invoice_id IS NOT NULL OR NEW.disposal_proceeds <> 0 THEN"),
    ("to_jsonb(NEW) -> 'disposal_entry_id')\n", "to_jsonb(NEW) -> 'disposal_entry_id', to_jsonb(NEW) -> 'disposal_invoice_id')\n"),
    ("to_jsonb(OLD) -> 'disposal_entry_id') THEN", "to_jsonb(OLD) -> 'disposal_entry_id', to_jsonb(OLD) -> 'disposal_invoice_id') THEN"),
]


def _guard(with_invoice: bool) -> str:
    function = PREVIOUS
    if with_invoice:
        for old, new in CHANGES:
            assert function.count(old) == 1, old
            function = function.replace(old, new)
    return function


class Migration(migrations.Migration):

    dependencies = [
        ('assets', '0005_revision_method'),
        ('invoicing', '0017_luxury_tax_places_refund_withholding'),
    ]

    operations = [
        migrations.AddField(
            model_name='fixedasset',
            name='disposal_invoice',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='disposed_assets', to='invoicing.invoice', verbose_name='sales invoice'),
        ),
        migrations.RunSQL(_guard(with_invoice=True), _guard(with_invoice=False)),
    ]
