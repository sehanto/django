"""PPnBM, places of business, faktur types (codes 06/09) and income tax given back by refunds.

* Issuing an invoice also snapshots each line's PPN besaran tertentu flag and PPnBM rate
  (next to the 0007 tax snapshot). Lines issued before have neither (no PPnBM).
* A VAT collector withholds PPN and PPnBM: a payment's ``vat_withheld`` is checked against
  both (0015), and so is a return's (0016).
* A sales return may give back income tax the buyer withheld (``income_tax_withheld``), at
  most what its receipts withheld less what earlier returns gave back.
"""

import importlib

import django.db.models.deletion
import django.db.models.expressions
from decimal import Decimal
from django.conf import settings
from django.db import migrations, models


GUARD_0007 = importlib.import_module("apps.invoicing.migrations.0007_line_tax_snapshot")
GUARD_0014 = importlib.import_module("apps.invoicing.migrations.0014_record_faktur_number")
GUARD_0016 = importlib.import_module("apps.invoicing.migrations.0016_facilities_and_withholding")
TAX_COLUMNS = GUARD_0007.TAX_COLUMNS + ", tax_specific_amount = t.is_specific_amount, tax_luxury_rate = t.luxury_rate"


def _invoice_guard(with_luxury: bool) -> str:
    function = GUARD_0014._invoice_guard(with_number=True)
    assert function.count(GUARD_0007.TAX_COLUMNS) == 1
    return function.replace(GUARD_0007.TAX_COLUMNS, TAX_COLUMNS) if with_luxury else function


PAYMENT_LIMIT = "                                                    WHERE p.invoice_id = i.id AND p.status = 'posted'), 0) <= i.tax_amount))"


def _payment_guard(with_luxury: bool) -> str:
    function = GUARD_0016._payment_guard(with_income_tax=True)
    assert function.count(PAYMENT_LIMIT) == 1
    return function.replace(PAYMENT_LIMIT, PAYMENT_LIMIT.replace("<= i.tax_amount))", "<= i.tax_amount + i.luxury_tax_amount))")) if with_luxury else function


RETURN_LIMIT = "NEW.vat_withheld > NEW.tax_amount\n"
RETURN_INSERT_CHECK = GUARD_0016.RETURN_INSERT_CHECK
INCOME_TAX_CHECK = """    IF NEW.income_tax_withheld > 0 AND (NEW.kind <> 'sales'
        OR NEW.income_tax_withheld
           + coalesce((SELECT sum(rn.income_tax_withheld) FROM invoicing_returnnote rn WHERE rn.invoice_id = NEW.invoice_id AND rn.status = 'posted'), 0)
           > coalesce((SELECT sum(p.income_tax_withheld) FROM invoicing_payment p WHERE p.invoice_id = NEW.invoice_id AND p.status = 'posted'), 0))
    THEN
        RAISE EXCEPTION 'A refund gives back at most the income tax the buyer withheld and still holds.'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
"""


def _return_guard(with_refunds: bool) -> str:
    function = GUARD_0016._return_guard(with_withholding=True)
    if not with_refunds:
        return function
    assert function.count(RETURN_LIMIT) == 1 and function.count(RETURN_INSERT_CHECK) == 1
    function = function.replace(RETURN_LIMIT, "NEW.vat_withheld > NEW.tax_amount + NEW.luxury_tax_amount\n")
    return function.replace(RETURN_INSERT_CHECK, INCOME_TAX_CHECK + RETURN_INSERT_CHECK)


class Migration(migrations.Migration):

    dependencies = [
        ('invoicing', '0016_facilities_and_withholding'),
        ('ledger', '0012_luxury_tax_account'),
        ('tenants', '0010_business_places'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='returnnote',
            name='return_is_credited_or_refunded',
        ),
        migrations.AddField(
            model_name='invoice',
            name='business_place',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='invoices', to='tenants.businessplace', verbose_name='place of business'),
        ),
        migrations.AddField(
            model_name='invoice',
            name='delivery_kind',
            field=models.CharField(blank=True, choices=[('', 'Normal delivery'), ('06', 'VAT refund for a tourist (code 06)'), ('09', 'Asset not originally for sale, Art. 16D (code 09)')], default='', max_length=2, verbose_name='type of delivery'),
        ),
        migrations.AddField(
            model_name='invoice',
            name='luxury_tax_amount',
            field=models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=18, verbose_name='PPnBM'),
        ),
        migrations.AddField(
            model_name='invoiceline',
            name='tax_luxury_rate',
            field=models.DecimalField(blank=True, decimal_places=2, editable=False, max_digits=5, null=True, verbose_name='PPnBM rate when issued (%)'),
        ),
        migrations.AddField(
            model_name='invoiceline',
            name='tax_specific_amount',
            field=models.BooleanField(blank=True, editable=False, null=True, verbose_name='PPN besaran tertentu when issued'),
        ),
        migrations.AddField(
            model_name='returnnote',
            name='income_tax_account',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='ledger.account', verbose_name='prepaid tax account'),
        ),
        migrations.AddField(
            model_name='returnnote',
            name='income_tax_kind',
            field=models.CharField(blank=True, choices=[('', 'None'), ('pph22', 'PPh 22 (e.g. 1.5% by a government treasurer)'), ('pph23', 'PPh 23 (2% on services)'), ('pph_final', 'PPh Final 0.5% (PP 55/2022, with a Surat Keterangan)')], default='', max_length=10, verbose_name='income tax withheld'),
        ),
        migrations.AddField(
            model_name='returnnote',
            name='income_tax_withheld',
            field=models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=18, verbose_name='income tax the buyer withheld'),
        ),
        migrations.AddField(
            model_name='returnnote',
            name='luxury_tax_amount',
            field=models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=18, verbose_name='PPnBM'),
        ),
        migrations.AddConstraint(
            model_name='returnnote',
            constraint=models.CheckConstraint(condition=models.Q(('credited__gte', 0), ('income_tax_withheld__gte', 0), ('refunded__gte', 0), ('total', django.db.models.expressions.CombinedExpression(django.db.models.expressions.CombinedExpression(django.db.models.expressions.CombinedExpression(models.F('credited'), '+', models.F('refunded')), '+', models.F('vat_withheld')), '+', models.F('income_tax_withheld'))), ('total__gt', 0), ('vat_withheld__gte', 0), ('vat_withheld__lte', django.db.models.expressions.CombinedExpression(models.F('tax_amount'), '+', models.F('luxury_tax_amount')))), name='return_is_credited_or_refunded'),
        ),
        migrations.RunSQL(_invoice_guard(with_luxury=True), _invoice_guard(with_luxury=False)),
        migrations.RunSQL(_payment_guard(with_luxury=True), _payment_guard(with_luxury=False)),
        migrations.RunSQL(_return_guard(with_refunds=True), _return_guard(with_refunds=False)),
    ]
