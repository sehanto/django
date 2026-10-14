"""The register can't rewrite history (assets migration 0002). These tests bypass the
services to prove the PostgreSQL guard."""

from contextlib import contextmanager
from datetime import date
from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction

from apps.assets import services
from apps.assets.models import DepreciationPosting, FixedAsset
from apps.ledger.models import JournalEntry

D = Decimal


@contextmanager
def refused():
    with pytest.raises(IntegrityError), transaction.atomic():
        yield


@pytest.fixture
def depreciated(organization, make_asset):
    asset = make_asset()
    services.post_depreciation(organization=organization, through=date(2026, 2, 28))
    return asset


def update(asset, **changes):
    FixedAsset.objects.filter(pk=asset.pk).update(**changes)


def test_figures_are_frozen_once_depreciated(depreciated, account):
    for change in ({"cost": D("1")}, {"useful_life_months": 12}, {"acquisition_date": date(2026, 3, 1)},
                   {"method": FixedAsset.Method.DECLINING_BALANCE}, {"depreciation_expense_account": account("6-1900")},
                   {"depreciated_before_until": date(2026, 6, 30)}):
        with refused():
            update(depreciated, **change)
    update(depreciated, name="Mesin kopi", description="Lantai 1")  # labels stay editable
    with refused():
        depreciated.delete()


def test_postings_are_append_only(depreciated):
    posting = depreciated.postings.first()
    with refused():
        DepreciationPosting.objects.filter(pk=posting.pk).update(amount=D("1"))
    with refused():
        posting.delete()
    with refused():  # a month that the entry's date doesn't match
        DepreciationPosting.objects.create(
            organization=depreciated.organization, asset=depreciated, year=2026, month=5, amount=D("1"), journal_entry=posting.journal_entry,
        )


def test_disposal_needs_its_journal_entry_and_is_final(organization, depreciated, account):
    with refused():
        update(depreciated, status=FixedAsset.Status.DISPOSED, disposed_on=date(2026, 3, 1))
    services.dispose_asset(asset=depreciated, on=date(2026, 3, 1), gain_loss_account=account("7-1900"))
    for change in ({"status": FixedAsset.Status.ACTIVE}, {"disposal_proceeds": D("5")}, {"disposed_on": date(2026, 3, 2)}):
        with refused():
            update(depreciated, **change)


def test_new_assets_start_in_use(organization, account):
    entry = JournalEntry.objects.filter(organization=organization).first()
    with refused():
        FixedAsset.objects.create(
            organization=organization, number="AT-X", name="x", asset_account=account("1-2300"), acquisition_date=date(2026, 1, 1),
            cost=D("100"), is_depreciable=False, method=FixedAsset.Method.STRAIGHT_LINE, status=FixedAsset.Status.DISPOSED,
            disposal_entry=entry,
        )
