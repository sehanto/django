"""The PPh 26 payable system account (non-resident employees) for existing organizations."""

from django.db import migrations, models


def add_account(apps, schema_editor):
    """Under the first free code from 2-1250."""
    Account = apps.get_model("ledger", "Account")
    Organization = apps.get_model("tenants", "Organization")
    for organization in Organization.objects.all():
        accounts = Account.objects.filter(organization=organization)
        if accounts.filter(system_role="pph26_payable").exists():
            continue
        codes = set(accounts.values_list("code", flat=True))
        code = next(f"2-{number}" for number in range(1250, 1260) if f"2-{number}" not in codes)
        Account.objects.create(
            organization=organization, code=code, name="Utang PPh Pasal 26", category="current_liability",
            system_role="pph26_payable", is_contra=False,
        )


class Migration(migrations.Migration):

    dependencies = [
        ('ledger', '0014_payroll_accounts'),
        ('tenants', '0012_invitation'),
    ]

    operations = [
        migrations.AlterField(
            model_name='account',
            name='system_role',
            field=models.CharField(blank=True, choices=[('receivable', 'Accounts receivable'), ('payable', 'Accounts payable'), ('output_vat', 'Output VAT (PPN Keluaran)'), ('luxury_tax', 'Sales tax on luxury goods (PPnBM)'), ('input_vat', 'Input VAT (PPN Masukan)'), ('sales_revenue', 'Sales revenue'), ('purchase_expense', 'Purchases / cost of sales'), ('retained_earnings', 'Retained earnings'), ('pph_final_expense', 'Final income tax expense'), ('pph_final_payable', 'Final income tax payable'), ('pph23_payable', 'Withheld PPh 23 payable'), ('pph4_2_payable', 'Withheld PPh 4(2) payable'), ('pph21_payable', 'Withheld PPh 21 payable'), ('pph26_payable', 'Withheld PPh 26 payable'), ('bpjs_payable', 'BPJS contributions payable')], max_length=30, null=True, verbose_name='system role'),
        ),
        migrations.RunPython(add_account, migrations.RunPython.noop),
    ]
