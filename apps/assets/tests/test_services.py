from datetime import date
from decimal import Decimal

import pytest
from django.core.management import call_command

from apps.assets import services
from apps.assets.models import DepreciationPosting, FixedAsset
from apps.assets.selectors import accumulated_depreciation, due_depreciation, register
from apps.core.exceptions import InvalidStateTransition, PeriodLocked, ValidationFailed
from apps.ledger.models import JournalEntry
from apps.ledger.selectors import account_balance

D = Decimal


def balance(account, code, on=None):
    acc = account(code)
    return account_balance(acc.organization, acc, as_of=on)


def test_registering_posts_nothing_and_uses_the_organization_policy(organization, account, make_asset):
    organization.depreciation_method = FixedAsset.Method.DECLINING_BALANCE
    organization.save(update_fields=["depreciation_method"])
    asset = make_asset()
    assert asset.number == "AT/2026/01/0001"
    assert asset.method == FixedAsset.Method.DECLINING_BALANCE
    assert not JournalEntry.objects.filter(organization=organization).exists()


@pytest.mark.parametrize("overrides, message", [
    ({"asset_account": "1-2900"}, "fixed asset or intangible asset account"),  # the contra account
    ({"asset_account": "6-1600"}, "fixed asset or intangible asset account"),
    ({"residual_value": D("4800000")}, "without a residual value"),
    ({"useful_life_months": None}, "useful life"),
    ({"accumulated_depreciation_account": "1-2300"}, "same kind as the asset account"),
    ({"depreciation_expense_account": "4-1000"}, "expense account"),
    ({"depreciated_before_until": date(2025, 12, 31)}, "before the acquisition date"),
])
def test_registration_is_validated(account, make_asset, overrides, message):
    resolved = {k: account(v) if isinstance(v, str) else v for k, v in overrides.items()}
    with pytest.raises(ValidationFailed, match=message):
        make_asset(**resolved)


def test_land_is_not_depreciated(organization, account, make_asset):
    land = make_asset(name="Tanah toko", cost="150000000", asset_account=account("1-2000"), is_depreciable=False, useful_life_months=None)
    assert land.useful_life_months is None and land.accumulated_depreciation_account is None
    assert land.schedule == [] and due_depreciation(organization, date(2026, 12, 31)) == {}


def test_due_months_are_caught_up_once_each(organization, account, make_asset, user):
    make_asset()  # 100.000 a month
    make_asset(name="Etalase", cost="1200000", months=12, acquired_on=date(2026, 2, 3))
    entries = services.post_depreciation(organization=organization, through=date(2026, 4, 10), created_by=user)

    assert [e.date for e in entries] == [date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31)]  # April hasn't ended
    assert entries[0].description == "Penyusutan aset tetap masa 01/2026" and entries[0].number.startswith("JA/")
    assert entries[1].lines.count() == 4  # both assets share the month's entry
    assert balance(account, "6-1600") == D("500000")  # 3 x 100.000 + 2 x 100.000
    assert balance(account, "1-2900") == D("500000")
    assert services.post_depreciation(organization=organization, through=date(2026, 4, 10)) == []
    assert DepreciationPosting.objects.filter(organization=organization).count() == 5


def test_a_locked_month_stops_the_run_without_skipping_it(organization, make_asset):
    make_asset()
    organization.lock_date = date(2026, 2, 28)
    organization.save(update_fields=["lock_date"])
    with pytest.raises(PeriodLocked):
        services.post_depreciation(organization=organization, through=date(2026, 4, 10))
    assert not DepreciationPosting.objects.exists()  # all or nothing


def test_depreciation_in_the_opening_balances_is_not_posted_again(organization, make_asset):
    asset = make_asset(depreciated_before_until=date(2026, 3, 10))  # becomes 31 March
    assert asset.depreciated_before_until == date(2026, 3, 31)
    assert asset.opening_depreciation == D("300000")
    entries = services.post_depreciation(organization=organization, through=date(2026, 5, 31))
    assert [e.date for e in entries] == [date(2026, 4, 30), date(2026, 5, 31)]
    assert accumulated_depreciation(asset) == D("500000")
    assert register(organization)[0].book_value == D("4300000")


def test_figures_are_fixed_once_depreciation_is_posted(organization, account, make_asset):
    asset = make_asset()
    services.update_asset(asset=asset, **{**_fields(asset), "cost": D("6000000")})  # nothing posted yet
    assert FixedAsset.objects.get(pk=asset.pk).cost == D("6000000")

    services.post_depreciation(organization=organization, through=date(2026, 1, 31))
    services.update_asset(asset=asset, **{**_fields(asset), "cost": D("1"), "name": "Mesin kopi"})
    asset.refresh_from_db()
    assert (asset.cost, asset.name) == (D("6000000"), "Mesin kopi")
    with pytest.raises(InvalidStateTransition):
        services.delete_asset(asset=asset)


def test_unused_assets_can_be_deleted(make_asset):
    asset = make_asset()
    services.delete_asset(asset=asset)
    assert not FixedAsset.objects.filter(pk=asset.pk).exists()


def test_selling_above_book_value_books_a_gain(organization, account, make_asset, user):
    asset = make_asset()
    services.dispose_asset(
        asset=asset, on=date(2026, 7, 10), proceeds=D("4500000"), cash_account=account("1-1100"),
        gain_loss_account=account("4-2000"), created_by=user,
    )
    asset.refresh_from_db()
    # January to June depreciated first (600.000), so the book value was 4.200.000.
    assert asset.status == FixedAsset.Status.DISPOSED and asset.postings.count() == 6
    assert balance(account, "1-2300") == D("-4800000")  # the purchase was never posted in this test
    assert balance(account, "1-2900") == 0
    assert balance(account, "4-2000") == D("300000")
    assert balance(account, "1-1100") == D("4500000")
    assert asset.disposal_entry.description == f"Pelepasan aset tetap {asset.number} Mesin espresso"
    # Nothing more is due for a disposed asset.
    assert due_depreciation(organization, date(2026, 12, 31)) == {}
    with pytest.raises(InvalidStateTransition):
        services.dispose_asset(asset=asset, on=date(2026, 8, 1))


def test_writing_off_books_a_loss(organization, account, make_asset):
    asset = make_asset()
    services.dispose_asset(asset=asset, on=date(2026, 2, 1), gain_loss_account=account("7-1900"))
    assert balance(account, "7-1900") == D("4700000")  # cost less January's depreciation


@pytest.mark.parametrize("kwargs, message", [
    ({"proceeds": D("100")}, "cash or bank account"),
    ({}, "gain or loss"),
    ({"on": date(2025, 12, 31)}, "before the acquisition date"),
])
def test_disposal_is_validated(make_asset, kwargs, message):
    asset = make_asset()
    with pytest.raises(ValidationFailed, match=message):
        services.dispose_asset(asset=asset, **{"on": date(2026, 3, 1), **kwargs})


def test_disposal_cannot_undercut_posted_depreciation(organization, account, make_asset):
    asset = make_asset()
    services.post_depreciation(organization=organization, through=date(2026, 5, 31))
    with pytest.raises(ValidationFailed, match="05/2026"):
        services.dispose_asset(asset=asset, on=date(2026, 3, 15), gain_loss_account=account("7-1900"))


def test_command_posts_for_every_organization_and_reports_locked_ones(organization, other_organization, make_asset, capsys):
    make_asset(acquired_on=date(2026, 1, 15))
    organization.lock_date = date(2026, 1, 31)
    organization.save(update_fields=["lock_date"])
    call_command("post_depreciation")
    out = capsys.readouterr()
    assert "0 depreciation entries; 1 organization(s) skipped" in out.out
    assert f"Organization {organization.pk}: depreciation not posted" in out.err

    organization.lock_date = None
    organization.save(update_fields=["lock_date"])
    call_command("post_depreciation")
    assert "0 organization(s) skipped" in capsys.readouterr().out
    assert DepreciationPosting.objects.filter(organization=organization).exists()


def _fields(asset):
    return {name: getattr(asset, name) for name in (
        "name", "description", "asset_account", "acquisition_date", "cost", "is_depreciable", "useful_life_months",
        "residual_value", "accumulated_depreciation_account", "depreciation_expense_account", "depreciated_before_until",
    )}
