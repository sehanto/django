"""Equity accounts for the organization's legal form (SAK EMKM Bab 13), for existing organizations.

Every organization used to get a sole proprietor's equity: Modal Pemilik and Prive. A partnership
names them after its partners; a PT has share capital and pays dividends, not prive; a cooperative
has its members' savings and reserves. Accounts nothing was posted to yet are renamed (or, for a
cooperative's Prive, deactivated); the others keep their name, as their history was recorded under
it, and the missing accounts are added next to them."""

from django.db import migrations

SOLE = {"3-1000": "Modal Pemilik", "3-2000": "Prive"}
EQUITY = {  # legal form -> (code, name, contra)
    "cv": [("3-1000", "Modal Sekutu", False), ("3-2000", "Prive Sekutu", True)],
    "firma": [("3-1000", "Modal Sekutu", False), ("3-2000", "Prive Sekutu", True)],
    "pt": [("3-1000", "Modal Saham", False), ("3-1100", "Tambahan Modal Disetor", False), ("3-2000", "Dividen", True)],
    "pt_perorangan": [("3-1000", "Modal Saham", False), ("3-1100", "Tambahan Modal Disetor", False), ("3-2000", "Dividen", True)],
    "koperasi": [("3-1000", "Simpanan Pokok", False), ("3-1100", "Simpanan Wajib", False), ("3-1200", "Cadangan", False)],
}


def _free_code(code: str, taken: set[str]) -> str:
    prefix, number = code.split("-")
    return next(f"{prefix}-{n}" for n in range(int(number), int(number) + 100) if f"{prefix}-{n}" not in taken)


def equity_for_legal_form(apps, schema_editor):
    Account = apps.get_model("ledger", "Account")
    JournalLine = apps.get_model("ledger", "JournalLine")
    Organization = apps.get_model("tenants", "Organization")
    for organization in Organization.objects.filter(legal_form__in=EQUITY):
        accounts = Account.objects.filter(organization=organization)
        unused = {
            account.code: account
            for account in accounts.filter(code__in=SOLE, category="equity")
            if account.name == SOLE[account.code] and not JournalLine.objects.filter(account=account).exists()
        }
        taken = set(accounts.values_list("code", flat=True))
        names = {name.lower() for name in accounts.values_list("name", flat=True)}
        for code, name, contra in EQUITY[organization.legal_form]:
            if name.lower() in names:
                continue
            account = unused.pop(code, None)
            if account is not None and account.is_contra == contra:
                account.name = name
                account.save(update_fields=["name"])
                continue
            code = _free_code(code, taken)
            taken.add(code)
            Account.objects.create(organization=organization, code=code, name=name, category="equity", is_contra=contra)
        for account in unused.values():  # a cooperative has no prive
            if account.is_contra:
                account.is_active = False
                account.save(update_fields=["is_active"])


class Migration(migrations.Migration):

    dependencies = [
        ('ledger', '0019_investments_and_deferrals'),
        ('tenants', '0013_structured_address'),
    ]

    operations = [
        migrations.RunPython(equity_for_legal_form, migrations.RunPython.noop),
    ]
