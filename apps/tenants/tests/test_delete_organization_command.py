from datetime import date
from decimal import Decimal
from unittest import mock

import pytest
from django.contrib.admin.sites import site
from django.core.management import CommandError, call_command
from django.test import RequestFactory

from apps.ledger.models import Account, JournalEntry
from apps.ledger.services import JournalLineInput, create_journal_entry
from apps.taxes.models import PphFinalAccrual, PphFinalPayment
from apps.taxes.services import record_pph_final_payment
from apps.tenants.models import Organization

COMMAND = "apps.tenants.management.commands.delete_organization"


@pytest.fixture
def books(organization, account):
    create_journal_entry(
        organization=organization, date=date(2026, 1, 2), description="Setoran modal awal",
        lines=[JournalLineInput(account("1-1100"), debit=Decimal("1000")), JournalLineInput(account("3-1000"), credit=Decimal("1000"))],
    )
    return organization


def test_deletes_with_yes(books, capsys):
    call_command("delete_organization", str(books.pk), "--yes", "--skip-export")
    assert not Organization.objects.filter(pk=books.pk).exists()
    assert not JournalEntry.objects.filter(organization_id=books.pk).exists()
    assert "1 journal entr" in capsys.readouterr().out


def test_deletes_tax_records_too(other_organization):
    org = other_organization

    def acc(code):
        return Account.objects.get(organization=org, code=code)

    create_journal_entry(
        organization=org, date=date(2026, 1, 20), description="Penjualan tunai",
        lines=[JournalLineInput(acc("1-1000"), debit=Decimal("2000000")), JournalLineInput(acc("4-1000"), credit=Decimal("2000000"))],
    )
    record_pph_final_payment(organization=org, year=2026, month=1, paid_on=date(2026, 2, 10), cash_account=acc("1-1000"))
    call_command("delete_organization", str(org.pk), "--yes", "--skip-export")
    assert not PphFinalAccrual.objects.filter(organization_id=org.pk).exists()
    assert not PphFinalPayment.objects.filter(organization_id=org.pk).exists()


def test_deletes_the_fixed_asset_register_too(organization, account):
    from apps.assets import services as assets
    from apps.assets.models import AssetRevision, DepreciationPosting, FixedAsset

    asset = assets.register_asset(
        organization=organization, name="Mesin", description="", asset_account=account("1-2300"), acquisition_date=date(2026, 1, 5),
        cost=Decimal("1200"), is_depreciable=True, useful_life_months=12, residual_value=Decimal("0"),
        accumulated_depreciation_account=account("1-2900"), depreciation_expense_account=account("6-1600"), depreciated_before_until=None,
    )
    assets.post_depreciation(organization=organization, through=date(2026, 2, 28))
    assets.revise_estimate(asset=asset, effective_from=date(2026, 3, 1), useful_life_months=24, reason="x")
    assets.dispose_asset(asset=asset, on=date(2026, 3, 2), gain_loss_account=account("7-1900"))
    call_command("delete_organization", str(organization.pk), "--yes", "--skip-export")
    assert not FixedAsset.objects.filter(organization_id=organization.pk).exists()
    assert not DepreciationPosting.objects.filter(organization_id=organization.pk).exists()
    assert not AssetRevision.objects.filter(organization_id=organization.pk).exists()


def test_asks_for_the_organization_name(books):
    with mock.patch("builtins.input", return_value="wrong"), pytest.raises(CommandError, match="nothing was deleted"):
        call_command("delete_organization", str(books.pk), "--skip-export")
    assert Organization.objects.filter(pk=books.pk).exists()

    with mock.patch("builtins.input", return_value=f"  {books.name} "):
        call_command("delete_organization", str(books.pk), "--skip-export")
    assert not Organization.objects.filter(pk=books.pk).exists()


def test_refuses_without_owner_privileges(books):
    with mock.patch(f"{COMMAND}.can_purge_ledger", return_value=False), pytest.raises(CommandError, match="DATABASE_OWNER_URL"):
        call_command("delete_organization", str(books.pk), "--yes", "--skip-export")
    assert JournalEntry.objects.filter(organization=books).exists()


def test_unknown_organization(db):
    with pytest.raises(CommandError, match="does not exist"):
        call_command("delete_organization", "999999", "--yes", "--skip-export")


def test_admin_cannot_delete_organizations(organization, user):
    request = RequestFactory().get("/")
    request.user = user
    model_admin = site._registry[Organization]
    assert model_admin.has_delete_permission(request, organization) is False
    assert f"manage.py delete_organization {organization.pk}" in str(model_admin.deletion(organization))


def test_deletes_the_prepayments_register_too(organization, account):
    from apps.deferrals import services as deferrals
    from apps.deferrals.domain import DeferralKind
    from apps.deferrals.models import Deferral, DeferralPosting

    deferrals.register_deferral(
        organization=organization, kind=DeferralKind.PREPAID_EXPENSE, description="Sewa", amount=Decimal("1200"),
        recorded_on=date(2026, 1, 5), starts_on=date(2026, 1, 1), months=12,
        balance_account=account("1-1600"), recognition_account=account("6-1100"), reclassify=True,
    )
    deferrals.post_deferrals(organization=organization, through=date(2026, 2, 28))
    call_command("delete_organization", str(organization.pk), "--yes", "--skip-export")
    assert not Deferral.objects.filter(organization_id=organization.pk).exists()
    assert not DeferralPosting.objects.filter(organization_id=organization.pk).exists()
