"""PPnBM (sales tax on luxury goods) collected on sales is owed to the state: a system account
"PPnBM Keluaran" (2-1150 in the default chart). Existing organizations get it too, under the
first free code from 2-1150."""

from django.db import migrations, models

CURRENT_LIABILITY = "current_liability"


def add_luxury_tax_account(apps, schema_editor):
    Account = apps.get_model("ledger", "Account")
    Organization = apps.get_model("tenants", "Organization")
    for organization in Organization.objects.all():
        accounts = Account.objects.filter(organization=organization)
        if accounts.filter(system_role="luxury_tax").exists():
            continue
        codes = set(accounts.values_list("code", flat=True))
        code = next(f"2-{number}" for number in range(1150, 1200) if f"2-{number}" not in codes)
        Account.objects.create(
            organization=organization, code=code, name="PPnBM Keluaran", category=CURRENT_LIABILITY,
            system_role="luxury_tax", is_contra=False,
        )


class Migration(migrations.Migration):

    dependencies = [
        ('ledger', '0011_inventory_source'),
        ('tenants', '0009_organization_nitku'),
    ]

    operations = [
        migrations.AlterField(
            model_name='account',
            name='system_role',
            field=models.CharField(blank=True, choices=[('receivable', 'Accounts receivable'), ('payable', 'Accounts payable'), ('output_vat', 'Output VAT (PPN Keluaran)'), ('luxury_tax', 'Sales tax on luxury goods (PPnBM)'), ('input_vat', 'Input VAT (PPN Masukan)'), ('sales_revenue', 'Sales revenue'), ('purchase_expense', 'Purchases / cost of sales'), ('retained_earnings', 'Retained earnings'), ('pph_final_expense', 'Final income tax expense'), ('pph_final_payable', 'Final income tax payable')], max_length=30, null=True, verbose_name='system role'),
        ),
        migrations.RunPython(add_luxury_tax_account, migrations.RunPython.noop),
    ]
