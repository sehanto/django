from django.core.management import call_command

from apps.assets.models import FixedAsset
from apps.inventory.selectors import reconcile, stock_summary
from apps.ledger.selectors import account_balances
from apps.reports.statements import build_trial_balance
from apps.tenants.models import Organization


def test_demo_books_balance_and_include_depreciation(organization, capsys):  # ``organization`` provides the default plan
    call_command("seed_demo", "--email", "demo-test@example.com")
    demo = Organization.objects.get(name="Kopi Nusantara Rina")
    asset = FixedAsset.objects.get(organization=demo)
    assert asset.postings.exists()  # months since the purchase are depreciated
    assert build_trial_balance(account_balances(demo), demo.created_at.date()).is_balanced
    [stock] = reconcile(demo, stock_summary(demo), demo.created_at.date())  # coffee is a tracked item
    assert stock.difference == 0 and stock.stock > 0
    assert "Demo ready" in capsys.readouterr().out
