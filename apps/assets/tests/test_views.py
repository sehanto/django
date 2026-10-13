from datetime import date
from decimal import Decimal

from django.urls import reverse

from apps.assets import services
from apps.assets.models import FixedAsset
from apps.tenants.roles import Role

D = Decimal


def form_data(account, **overrides):
    return {
        "name": "Mesin espresso", "description": "", "asset_account": account("1-2300").pk, "acquisition_date": "2026-01-15",
        "cost": "4800000", "is_depreciable": "on", "useful_life_months": "48", "residual_value": "",
        "accumulated_depreciation_account": account("1-2900").pk, "depreciation_expense_account": account("6-1600").pk,
        "depreciated_before_until": "", **overrides,
    }


def test_register_post_and_dispose_through_the_pages(auth_client, organization, account):
    response = auth_client.post(reverse("assets:create"), form_data(account))
    asset = FixedAsset.objects.get(organization=organization)
    assert response.url == reverse("assets:detail", args=[asset.pk])

    listing = auth_client.get(reverse("assets:list"))
    assert asset.number in listing.content.decode() and listing.context["due_months"] > 0  # months since January
    auth_client.post(reverse("assets:post_depreciation"))
    assert asset.postings.exists()
    assert auth_client.get(reverse("assets:list")).context["due_months"] == 0

    detail = auth_client.get(reverse("assets:detail", args=[asset.pk])).content.decode()
    assert asset.postings.first().journal_entry.number in detail

    auth_client.post(reverse("assets:dispose", args=[asset.pk]), {
        "on": date.today().isoformat(), "proceeds": "0", "gain_loss_account": account("7-1100").pk,
    })
    asset.refresh_from_db()
    assert asset.status == FixedAsset.Status.DISPOSED


def test_depreciable_assets_need_a_life(auth_client, organization, account):
    response = auth_client.post(reverse("assets:create"), form_data(account, useful_life_months=""))
    assert response.status_code == 200 and "useful_life_months" in response.context["form"].errors
    assert not FixedAsset.objects.exists()


def test_figures_are_shown_fixed_after_depreciation(auth_client, organization, account, make_asset):
    asset = make_asset()
    services.post_depreciation(organization=organization, through=date(2026, 1, 31))
    form = auth_client.get(reverse("assets:update", args=[asset.pk])).context["form"]
    assert form.fields["cost"].disabled and not form.fields["name"].disabled
    auth_client.post(reverse("assets:update", args=[asset.pk]), form_data(account, name="Mesin kopi", cost="1"))
    asset.refresh_from_db()
    assert (asset.name, asset.cost) == ("Mesin kopi", D("4800000"))


def test_viewers_see_but_do_not_change(client, make_member, organization, make_asset):
    asset = make_asset()
    client.force_login(make_member(Role.VIEWER))
    assert client.get(reverse("assets:detail", args=[asset.pk])).status_code == 200
    assert client.post(reverse("assets:post_depreciation")).status_code == 403
    assert not asset.postings.exists()


def test_other_organizations_assets_are_not_found(auth_client, other_organization):
    from apps.ledger.models import Account

    def acc(code):
        return Account.objects.get(organization=other_organization, code=code)

    other = services.register_asset(
        organization=other_organization, name="x", description="", asset_account=acc("1-2300"), acquisition_date=date(2026, 1, 1),
        cost=D("1000"), is_depreciable=False, useful_life_months=None, residual_value=D("0"),
        accumulated_depreciation_account=None, depreciation_expense_account=None, depreciated_before_until=None,
    )
    assert auth_client.get(reverse("assets:detail", args=[other.pk])).status_code == 404
    assert auth_client.post(reverse("assets:dispose", args=[other.pk])).status_code == 404


def test_register_reconciles_with_the_ledger(auth_client, organization, account, make_asset):
    from apps.ledger.services import JournalLineInput, create_journal_entry

    make_asset()  # cost 4.800.000, its purchase not posted yet
    rows = {r.account.code: r for r in auth_client.get(reverse("assets:list")).context["reconciliation"]}
    assert (rows["1-2300"].register, rows["1-2300"].ledger, rows["1-2300"].difference) == (D("4800000"), 0, D("-4800000"))

    create_journal_entry(
        organization=organization, date=date(2026, 1, 15), description="Pembelian mesin espresso",
        lines=[JournalLineInput(account("1-2300"), debit=D("4800000")), JournalLineInput(account("1-1100"), credit=D("4800000"))],
    )
    rows = {r.account.code: r for r in auth_client.get(reverse("assets:list")).context["reconciliation"]}
    assert rows["1-2300"].difference == 0
