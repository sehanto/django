from datetime import date
from decimal import Decimal

import pytest
from django.utils import translation

from apps.assets import services


@pytest.fixture(autouse=True)
def english():
    """Messages are matched in English; the default language is Indonesian."""
    with translation.override("en"):
        yield


@pytest.fixture
def make_asset(organization, account):
    def make(name="Mesin espresso", cost="4800000", months=48, acquired_on=date(2026, 1, 15), **overrides):
        data = dict(
            name=name, description="", asset_account=account("1-2300"), acquisition_date=acquired_on, cost=Decimal(cost),
            is_depreciable=True, useful_life_months=months, residual_value=Decimal("0"),
            accumulated_depreciation_account=account("1-2900"), depreciation_expense_account=account("6-1600"),
            depreciated_before_until=None,
        )
        data.update(overrides)
        return services.register_asset(organization=organization, **data)

    return make
