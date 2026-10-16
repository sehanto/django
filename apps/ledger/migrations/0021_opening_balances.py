"""The opening balance equity account (3-9000 Ekuitas Saldo Awal) for existing organizations: the
other side of balances brought over from earlier books (apps.uploads)."""

from django.db import migrations, models


def add_opening_balance_account(apps, schema_editor):
    Account = apps.get_model("ledger", "Account")
    Organization = apps.get_model("tenants", "Organization")
    having = Account.objects.filter(system_role="opening_balance").values("organization_id")
    for organization in Organization.objects.exclude(pk__in=having):
        taken = set(Account.objects.filter(organization=organization).values_list("code", flat=True))
        code = next(f"3-{n}" for n in range(9000, 10000) if f"3-{n}" not in taken)
        Account.objects.create(
            organization=organization, code=code, name="Ekuitas Saldo Awal", category="equity", system_role="opening_balance",
        )


class Migration(migrations.Migration):

    dependencies = [
        ("ledger", "0020_equity_by_legal_form"),
        ("tenants", "0015_plan_tiers_and_trial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="account",
            name="system_role",
            field=models.CharField(
                blank=True,
                choices=[
                    ("receivable", "Accounts receivable"),
                    ("payable", "Accounts payable"),
                    ("output_vat", "Output VAT (PPN Keluaran)"),
                    ("luxury_tax", "Sales tax on luxury goods (PPnBM)"),
                    ("input_vat", "Input VAT (PPN Masukan)"),
                    ("sales_revenue", "Sales revenue"),
                    ("purchase_expense", "Purchases / cost of sales"),
                    ("retained_earnings", "Retained earnings"),
                    ("pph_final_expense", "Final income tax expense"),
                    ("pph_final_payable", "Final income tax payable"),
                    ("pph23_payable", "Withheld PPh 23 payable"),
                    ("pph4_2_payable", "Withheld PPh 4(2) payable"),
                    ("pph21_payable", "Withheld PPh 21 payable"),
                    ("pph26_payable", "Withheld PPh 26 payable"),
                    ("bpjs_payable", "BPJS contributions payable"),
                    ("bad_debt_expense", "Bad debts written off"),
                    ("opening_balance", "Opening balance equity"),
                ],
                max_length=30,
                null=True,
                verbose_name="system role",
            ),
        ),
        migrations.AlterField(
            model_name="journalentry",
            name="source",
            field=models.CharField(
                choices=[
                    ("manual", "General journal"),
                    ("sales_invoice", "Sales invoice"),
                    ("purchase_invoice", "Purchase bill"),
                    ("payment", "Payment"),
                    ("tax", "Tax"),
                    ("fixed_asset", "Fixed asset"),
                    ("inventory", "Inventory"),
                    ("payroll", "Payroll"),
                    ("reversal", "Reversal"),
                    ("write_off", "Receivable written off"),
                    ("deferral", "Prepayment spread over its term"),
                    ("opening", "Opening balances"),
                ],
                default="manual",
                max_length=20,
                verbose_name="source",
            ),
        ),
        migrations.RunPython(add_opening_balance_account, migrations.RunPython.noop),
    ]
