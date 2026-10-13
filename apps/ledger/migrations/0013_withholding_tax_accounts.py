"""Income tax withheld from suppliers (PPh 23 and 4(2)) is owed to the state until the monthly
deposit: two system accounts (2-1210, 2-1220 in the default chart). Existing organizations get
them too, under the first free codes from there."""

from django.db import migrations, models

ACCOUNTS = [("pph23_payable", "Utang PPh Pasal 23", 1210), ("pph4_2_payable", "Utang PPh Pasal 4 ayat (2)", 1220)]


def add_accounts(apps, schema_editor):
    Account = apps.get_model("ledger", "Account")
    Organization = apps.get_model("tenants", "Organization")
    for organization in Organization.objects.all():
        accounts = Account.objects.filter(organization=organization)
        for role, name, first in ACCOUNTS:
            if accounts.filter(system_role=role).exists():
                continue
            codes = set(accounts.values_list("code", flat=True))
            code = next(f"2-{number}" for number in range(first, first + 10) if f"2-{number}" not in codes)
            Account.objects.create(
                organization=organization, code=code, name=name, category="current_liability", system_role=role, is_contra=False,
            )


class Migration(migrations.Migration):

    dependencies = [
        ('ledger', '0012_luxury_tax_account'),
        ('tenants', '0011_retailer'),
    ]

    operations = [
        migrations.AlterField(
            model_name='account',
            name='system_role',
            field=models.CharField(blank=True, choices=[('receivable', 'Accounts receivable'), ('payable', 'Accounts payable'), ('output_vat', 'Output VAT (PPN Keluaran)'), ('luxury_tax', 'Sales tax on luxury goods (PPnBM)'), ('input_vat', 'Input VAT (PPN Masukan)'), ('sales_revenue', 'Sales revenue'), ('purchase_expense', 'Purchases / cost of sales'), ('retained_earnings', 'Retained earnings'), ('pph_final_expense', 'Final income tax expense'), ('pph_final_payable', 'Final income tax payable'), ('pph23_payable', 'Withheld PPh 23 payable'), ('pph4_2_payable', 'Withheld PPh 4(2) payable')], max_length=30, null=True, verbose_name='system role'),
        ),
        migrations.RunPython(add_accounts, migrations.RunPython.noop),
    ]
