"""Income tax withheld from suppliers (PPh 23 and 4(2)).

* Payments: a receipt records income tax the customer withheld (PPh 22, 23 or Final), a
  payment to a supplier the tax we withheld (PPh 23 or 4(2)); the bukti potong number may
  be set once afterwards, on a payment that withheld tax (it comes from Coretax later).
* Returns: a purchase return refunded by the supplier also gives back what we withheld on it.
"""

import importlib

import django.core.validators
from decimal import Decimal
from django.db import migrations, models


GUARD_0017 = importlib.import_module("apps.invoicing.migrations.0017_luxury_tax_places_refund_withholding")
SALES_ONLY = (
    "           OR (NEW.income_tax_withheld > 0 AND NOT EXISTS (\n"
    "               SELECT 1 FROM invoicing_invoice i WHERE i.id = NEW.invoice_id AND i.kind = 'sales'))\n"
)
BY_SIDE = (
    "           OR (NEW.income_tax_withheld > 0 AND NOT EXISTS (\n"
    "               SELECT 1 FROM invoicing_invoice i WHERE i.id = NEW.invoice_id AND (\n"
    "                   (i.kind = 'sales' AND NEW.income_tax_kind IN ('pph22', 'pph23', 'pph_final'))\n"
    "                   OR (i.kind = 'purchase' AND NEW.income_tax_kind IN ('pph23', 'pph4_2')))))\n"
)
FROZEN = "(to_jsonb(NEW) - 'status' - 'updated_at' - 'created_by_id') IS DISTINCT FROM (to_jsonb(OLD) - 'status' - 'updated_at' - 'created_by_id')"
SLIP_CHECK = """IF NEW.withholding_slip IS DISTINCT FROM OLD.withholding_slip
       AND (OLD.withholding_slip <> '' OR NEW.withholding_slip = '' OR OLD.income_tax_withheld = 0)
    THEN
        RAISE EXCEPTION 'Payment % keeps its bukti potong number.', OLD.number USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF """


def _payment_guard(with_payer: bool) -> str:
    function = GUARD_0017._payment_guard(with_luxury=True)
    if not with_payer:
        return function
    assert function.count(SALES_ONLY) == 1 and function.count("    IF " + FROZEN) == 1
    function = function.replace(SALES_ONLY, BY_SIDE)
    frozen = FROZEN.replace("'status'", "'status' - 'withholding_slip'")
    return function.replace("    IF " + FROZEN, "    " + SLIP_CHECK + frozen)


def _return_guard(with_purchases: bool) -> str:
    function = GUARD_0017._return_guard(with_refunds=True)
    old = "    IF NEW.income_tax_withheld > 0 AND (NEW.kind <> 'sales'\n        OR NEW.income_tax_withheld"
    assert function.count(old) == 1
    return function.replace(old, "    IF NEW.income_tax_withheld > 0 AND (\n        NEW.income_tax_withheld") if with_purchases else function


class Migration(migrations.Migration):

    dependencies = [
        ('invoicing', '0017_luxury_tax_places_refund_withholding'),
    ]

    operations = [
        migrations.AddField(
            model_name='payment',
            name='income_tax_base',
            field=models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=18, validators=[django.core.validators.MinValueValidator(0)], verbose_name='income tax base'),
        ),
        migrations.AddField(
            model_name='payment',
            name='income_tax_rate',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=5, null=True, validators=[django.core.validators.MinValueValidator(Decimal('0.01')), django.core.validators.MaxValueValidator(100)], verbose_name='income tax rate (%)'),
        ),
        migrations.AlterField(
            model_name='payment',
            name='income_tax_kind',
            field=models.CharField(blank=True, choices=[('', 'None'), ('pph22', 'PPh 22 (e.g. 1.5% by a government treasurer)'), ('pph23', 'PPh 23 (2% on services)'), ('pph_final', 'PPh Final 0.5% (PP 55/2022, with a Surat Keterangan)'), ('pph4_2', 'PPh 4(2) final (e.g. 10% on rent of land or buildings)')], default='', max_length=10, verbose_name='income tax withheld'),
        ),
        migrations.AlterField(
            model_name='returnnote',
            name='income_tax_kind',
            field=models.CharField(blank=True, choices=[('', 'None'), ('pph22', 'PPh 22 (e.g. 1.5% by a government treasurer)'), ('pph23', 'PPh 23 (2% on services)'), ('pph_final', 'PPh Final 0.5% (PP 55/2022, with a Surat Keterangan)'), ('pph4_2', 'PPh 4(2) final (e.g. 10% on rent of land or buildings)')], default='', max_length=10, verbose_name='income tax withheld'),
        ),
        migrations.RunSQL(_payment_guard(with_payer=True), _payment_guard(with_payer=False)),
        migrations.RunSQL(_return_guard(with_purchases=True), _return_guard(with_purchases=False)),
    ]
