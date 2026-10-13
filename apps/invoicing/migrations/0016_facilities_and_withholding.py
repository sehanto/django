"""PPN facilities (Faktur Pajak codes 07/08), buyers identified without an NPWP, branch
NITKUs, income tax withheld by customers, and returns of PPN a VAT collector withheld.

* Invoices get their facility (frozen at issue like every other column, 0014) and snapshot
  the buyer's identity document, country and NITKU and the seller's NITKU.
* Payments: only a sales invoice's receipt records income tax withheld by the buyer.
* Returns: the PPN of a sales return that the buyer (a VAT collector) withheld is left out of
  the credit and refund, up to what the buyer still holds: its payments' withheld PPN less
  what earlier returns already left out.
"""

import importlib

import django.core.validators
import django.db.models.deletion
import django.db.models.expressions
from decimal import Decimal
from django.conf import settings
from django.db import migrations, models


PREVIOUS_PAYMENT_GUARD = importlib.import_module("apps.invoicing.migrations.0015_vat_collectors")._payment_guard(with_withholding=True)
PAYMENT_CHECK = "    THEN\n            RAISE EXCEPTION 'A payment is recorded for an open invoice with a posted journal entry.'"
INCOME_TAX_CHECK = (
    "           OR (NEW.income_tax_withheld > 0 AND NOT EXISTS (\n"
    "               SELECT 1 FROM invoicing_invoice i WHERE i.id = NEW.invoice_id AND i.kind = 'sales'))\n"
)


def _payment_guard(with_income_tax: bool) -> str:
    assert PREVIOUS_PAYMENT_GUARD.count(PAYMENT_CHECK) == 1
    if not with_income_tax:
        return PREVIOUS_PAYMENT_GUARD
    return PREVIOUS_PAYMENT_GUARD.replace(PAYMENT_CHECK, INCOME_TAX_CHECK + PAYMENT_CHECK)


RETURN_SQL = importlib.import_module("apps.invoicing.migrations.0013_voidable_returns").RETURN_GUARD_SQL
PREVIOUS_RETURN_GUARD = RETURN_SQL[:RETURN_SQL.index("$$;") + 3]
RETURN_INSERT_CHECK = "    IF NEW.status <> 'posted' OR NOT EXISTS ("
RETURN_WITHHOLDING_CHECK = """    IF NEW.vat_withheld > 0 AND (NEW.kind <> 'sales' OR NEW.vat_withheld > NEW.tax_amount
        OR NEW.vat_withheld
           + coalesce((SELECT sum(rn.vat_withheld) FROM invoicing_returnnote rn WHERE rn.invoice_id = NEW.invoice_id AND rn.status = 'posted'), 0)
           > coalesce((SELECT sum(p.vat_withheld) FROM invoicing_payment p WHERE p.invoice_id = NEW.invoice_id AND p.status = 'posted'), 0))
    THEN
        RAISE EXCEPTION 'A return leaves out at most the PPN the buyer withheld and still holds.'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
"""


def _return_guard(with_withholding: bool) -> str:
    assert PREVIOUS_RETURN_GUARD.count(RETURN_INSERT_CHECK) == 1
    if not with_withholding:
        return PREVIOUS_RETURN_GUARD
    return PREVIOUS_RETURN_GUARD.replace(RETURN_INSERT_CHECK, RETURN_WITHHOLDING_CHECK + RETURN_INSERT_CHECK)


class Migration(migrations.Migration):

    dependencies = [
        ('invoicing', '0015_vat_collectors'),
        ('ledger', '0011_inventory_source'),
        ('tenants', '0009_organization_nitku'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='returnnote',
            name='return_is_credited_or_refunded',
        ),
        migrations.AddField(
            model_name='invoice',
            name='contact_country',
            field=models.CharField(blank=True, editable=False, max_length=3, verbose_name='contact country'),
        ),
        migrations.AddField(
            model_name='invoice',
            name='contact_id_number',
            field=models.CharField(blank=True, editable=False, max_length=30, verbose_name='contact identity document number'),
        ),
        migrations.AddField(
            model_name='invoice',
            name='contact_id_type',
            field=models.CharField(blank=True, choices=[('', 'None: the NPWP identifies the buyer'), ('National ID', 'NIK (Indonesian identity card)'), ('Passport', 'Passport'), ('Other ID', 'Other identity document')], editable=False, max_length=12, verbose_name='contact identity document'),
        ),
        migrations.AddField(
            model_name='invoice',
            name='contact_nitku',
            field=models.CharField(blank=True, editable=False, max_length=22, verbose_name='contact NITKU'),
        ),
        migrations.AddField(
            model_name='invoice',
            name='facility_info',
            field=models.CharField(blank=True, max_length=10, verbose_name='facility (keterangan tambahan)'),
        ),
        migrations.AddField(
            model_name='invoice',
            name='facility_stamp',
            field=models.CharField(blank=True, max_length=10, verbose_name='facility stamp (cap fasilitas)'),
        ),
        migrations.AddField(
            model_name='invoice',
            name='seller_nitku',
            field=models.CharField(blank=True, editable=False, max_length=22, verbose_name='seller NITKU'),
        ),
        migrations.AddField(
            model_name='invoice',
            name='supporting_document',
            field=models.CharField(blank=True, help_text='For a facility, if any: e.g. the SPPB submission number, or PJKEK#number for a special economic zone.', max_length=100, verbose_name='supporting document'),
        ),
        migrations.AddField(
            model_name='invoice',
            name='supporting_document_date',
            field=models.DateField(blank=True, null=True, verbose_name='supporting document date'),
        ),
        migrations.AddField(
            model_name='invoice',
            name='vat_facility',
            field=models.CharField(blank=True, choices=[('', 'No facility'), ('07', 'PPN not collected or borne by the government (code 07)'), ('08', 'PPN exempt (code 08)')], default='', max_length=2, verbose_name='PPN facility'),
        ),
        migrations.AddField(
            model_name='invoice',
            name='vat_not_collected',
            field=models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=18, verbose_name='PPN not collected (facility)'),
        ),
        migrations.AddField(
            model_name='payment',
            name='income_tax_account',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='ledger.account', verbose_name='prepaid tax account'),
        ),
        migrations.AddField(
            model_name='payment',
            name='income_tax_kind',
            field=models.CharField(blank=True, choices=[('', 'None'), ('pph22', 'PPh 22 (e.g. 1.5% by a government treasurer)'), ('pph23', 'PPh 23 (2% on services)'), ('pph_final', 'PPh Final 0.5% (PP 55/2022, with a Surat Keterangan)')], default='', max_length=10, verbose_name='income tax withheld'),
        ),
        migrations.AddField(
            model_name='payment',
            name='income_tax_withheld',
            field=models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=18, validators=[django.core.validators.MinValueValidator(0)], verbose_name='income tax withheld by the buyer'),
        ),
        migrations.AddField(
            model_name='payment',
            name='withholding_slip',
            field=models.CharField(blank=True, help_text='The bukti potong number from the buyer.', max_length=50, verbose_name='withholding slip no.'),
        ),
        migrations.AddField(
            model_name='returnnote',
            name='vat_not_collected',
            field=models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=18, verbose_name='PPN not collected (facility)'),
        ),
        migrations.AddField(
            model_name='returnnote',
            name='vat_withheld',
            field=models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=18, verbose_name='PPN withheld by the buyer'),
        ),
        migrations.AddConstraint(
            model_name='payment',
            constraint=models.CheckConstraint(condition=models.Q(('income_tax_withheld__gte', 0), ('income_tax_withheld__lte', django.db.models.expressions.CombinedExpression(models.F('amount'), '-', models.F('vat_withheld'))), models.Q(models.Q(('income_tax_kind', ''), ('income_tax_withheld', 0)), models.Q(models.Q(('income_tax_kind', ''), _negated=True), ('income_tax_withheld__gt', 0)), _connector='OR')), name='payment_income_tax_withheld_within_amount'),
        ),
        migrations.AddConstraint(
            model_name='returnnote',
            constraint=models.CheckConstraint(condition=models.Q(('credited__gte', 0), ('refunded__gte', 0), ('total', django.db.models.expressions.CombinedExpression(django.db.models.expressions.CombinedExpression(models.F('credited'), '+', models.F('refunded')), '+', models.F('vat_withheld'))), ('total__gt', 0), ('vat_withheld__gte', 0), ('vat_withheld__lte', models.F('tax_amount'))), name='return_is_credited_or_refunded'),
        ),
        migrations.RunSQL(_payment_guard(with_income_tax=True), _payment_guard(with_income_tax=False)),
        migrations.RunSQL(_return_guard(with_withholding=True), _return_guard(with_withholding=False)),
    ]
