from django.core.management import call_command
from django.utils import timezone

from apps.assets.models import FixedAsset
from apps.inventory.selectors import reconcile, stock_summary
from apps.ledger.selectors import account_balances
from apps.reports.statements import build_trial_balance
from apps.tenants.models import Organization


def test_demo_books_balance_and_include_depreciation(organization, regions, capsys):  # ``organization`` provides the default plan
    regions("34.71.05.1001", "34.04.07.2001", "34.71.10.1002", "34.02.08.2004", "33.23.03.1003", "34.71.04.1001")
    call_command("seed_demo", "--email", "demo-test@example.com")
    demo = Organization.objects.get(name="Kopi Nusantara Rina")
    asset = FixedAsset.objects.get(organization=demo)
    assert asset.postings.exists()  # months since the purchase are depreciated
    created_on = timezone.localdate(demo.created_at)  # the demo dates by local time, not UTC (00:00-07:00 WIB differ)
    assert build_trial_balance(account_balances(demo), created_on).is_balanced
    [stock] = reconcile(demo, stock_summary(demo), created_on)  # coffee is a tracked item
    assert stock.difference == 0 and stock.stock > 0
    assert "Demo ready" in capsys.readouterr().out
