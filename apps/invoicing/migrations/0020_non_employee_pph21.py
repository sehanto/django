"""PPh 21 withheld from a fee paid to an individual who isn't an employee (a purchase)."""

import importlib

from django.db import migrations, models

GUARD_0018 = importlib.import_module("apps.invoicing.migrations.0018_payer_withholding")
PURCHASE_KINDS = "(i.kind = 'purchase' AND NEW.income_tax_kind IN ('pph23', 'pph4_2'))"


def _payment_guard(with_pph21: bool) -> str:
    function = GUARD_0018._payment_guard(with_payer=True)
    assert function.count(PURCHASE_KINDS) == 1
    return function.replace(PURCHASE_KINDS, PURCHASE_KINDS.replace("'pph4_2'", "'pph4_2', 'pph21'")) if with_pph21 else function


class Migration(migrations.Migration):

    dependencies = [
        ('invoicing', '0019_coretax_submissions'),
    ]

    operations = [
        migrations.AlterField(
            model_name='payment',
            name='income_tax_kind',
            field=models.CharField(blank=True, choices=[('', 'None'), ('pph22', 'PPh 22 (e.g. 1.5% by a government treasurer)'), ('pph23', 'PPh 23 (2% on services)'), ('pph_final', 'PPh Final 0.5% (PP 55/2022, with a Surat Keterangan)'), ('pph4_2', 'PPh 4(2) final (e.g. 10% on rent of land or buildings)'), ('pph21', 'PPh 21 (fee to an individual who is not an employee)')], default='', max_length=10, verbose_name='income tax withheld'),
        ),
        migrations.AlterField(
            model_name='returnnote',
            name='income_tax_kind',
            field=models.CharField(blank=True, choices=[('', 'None'), ('pph22', 'PPh 22 (e.g. 1.5% by a government treasurer)'), ('pph23', 'PPh 23 (2% on services)'), ('pph_final', 'PPh Final 0.5% (PP 55/2022, with a Surat Keterangan)'), ('pph4_2', 'PPh 4(2) final (e.g. 10% on rent of land or buildings)'), ('pph21', 'PPh 21 (fee to an individual who is not an employee)')], default='', max_length=10, verbose_name='income tax withheld'),
        ),
        migrations.RunSQL(_payment_guard(with_pph21=True), _payment_guard(with_pph21=False)),
    ]
