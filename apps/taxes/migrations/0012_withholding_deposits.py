"""Deposits of income tax withheld from suppliers are immutable like PPh Final payments
(taxes 0006): inserted as posted with a posted journal entry, then only posted -> void after
that entry is reversed, never deleted. ``ledger_purge_organization`` removes them first."""

import importlib

import django.core.validators
import django.db.models.deletion
from decimal import Decimal
from django.conf import settings
from django.db import migrations, models


PPH_FINAL_GUARD = importlib.import_module("apps.taxes.migrations.0006_immutable_pph_final_payments")
GUARD_SQL = (
    PPH_FINAL_GUARD.GUARD_SQL.replace("taxes_guard_pph_final_payment", "taxes_guard_withholding_deposit")
    .replace("taxes_pphfinalpayment_guard", "taxes_withholdingdeposit_guard")
    .replace("ON taxes_pphfinalpayment", "ON taxes_withholdingdeposit")
    .replace("NEW.tax_amount <= 0", "NEW.amount <= 0")
    .replace("PPh Final payment", "Withholding tax deposit")
)
DROP_SQL = """
DROP TRIGGER IF EXISTS taxes_withholdingdeposit_guard ON taxes_withholdingdeposit;
DROP FUNCTION IF EXISTS taxes_guard_withholding_deposit();
"""
PURGE = importlib.import_module("apps.invoicing.migrations.0011_immutable_returns")
FIRST = "    PERFORM set_config('ledger.purge', 'on', true);\n"


def _purge_function(with_deposits: bool) -> str:
    sql = PURGE._purge_function(with_returns=True)
    assert sql.count(FIRST) == 1
    if with_deposits:
        sql = sql.replace(FIRST, FIRST + "    DELETE FROM taxes_withholdingdeposit WHERE organization_id = org_id;\n")
    return sql


class Migration(migrations.Migration):

    dependencies = [
        ('ledger', '0013_withholding_tax_accounts'),
        ('taxes', '0011_specific_amount_and_luxury_rates'),
        ('tenants', '0011_retailer'),
        ('invoicing', '0011_immutable_returns'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='WithholdingDeposit',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='created')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='updated')),
                ('kind', models.CharField(choices=[('pph23', 'PPh 23 (e.g. 2% on services)'), ('pph4_2', 'PPh 4(2) final (e.g. 10% on rent of land or buildings)')], max_length=10, verbose_name='income tax')),
                ('year', models.PositiveSmallIntegerField(verbose_name='year')),
                ('month', models.PositiveSmallIntegerField(choices=[(1, 'January'), (2, 'February'), (3, 'March'), (4, 'April'), (5, 'May'), (6, 'June'), (7, 'July'), (8, 'August'), (9, 'September'), (10, 'October'), (11, 'November'), (12, 'December')], verbose_name='month')),
                ('amount', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=18, verbose_name='amount')),
                ('paid_on', models.DateField(verbose_name='payment date')),
                ('ntpn', models.CharField(blank=True, max_length=32, validators=[django.core.validators.RegexValidator('^[A-Z0-9]{16}$', 'NTPN must be 16 letters or digits.')], verbose_name='NTPN')),
                ('status', models.CharField(choices=[('posted', 'Recorded'), ('void', 'Void')], default='posted', max_length=10, verbose_name='status')),
                ('cash_account', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='ledger.account', verbose_name='paid from')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('journal_entry', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='ledger.journalentry')),
                ('organization', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='+', to='tenants.organization', verbose_name='organization')),
            ],
            options={
                'verbose_name': 'withholding tax deposit',
                'verbose_name_plural': 'withholding tax deposits',
                'ordering': ['-year', '-month', 'kind'],
                'constraints': [models.UniqueConstraint(condition=models.Q(('status', 'posted')), fields=('organization', 'kind', 'year', 'month'), name='unique_withholding_deposit_period'), models.CheckConstraint(condition=models.Q(('amount__gt', 0)), name='withholding_deposit_positive')],
            },
        ),
        migrations.RunSQL(GUARD_SQL, DROP_SQL),
        migrations.RunSQL(_purge_function(with_deposits=True), _purge_function(with_deposits=False)),
    ]
