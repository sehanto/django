from decimal import Decimal

import pytest
from django.core.cache import cache

from apps.tenants.models import Plan
from apps.tenants.services import get_default_plan


@pytest.fixture(autouse=True)
def _fresh_fake_gateway():
    cache.clear()  # the fake gateway keeps its state in the cache


@pytest.fixture
def plans(db):
    free = get_default_plan()
    usaha, _ = Plan.objects.update_or_create(
        code="usaha", defaults={"name": "Usaha", "monthly_price": Decimal("99000"), "yearly_price": Decimal("990000"), "max_users": 3, "sort_order": 2},
    )
    pro, _ = Plan.objects.update_or_create(
        code="pro", defaults={"name": "Pro", "monthly_price": Decimal("249000"), "yearly_price": Decimal("2490000"), "max_users": 10, "sort_order": 3},
    )
    return {"free": free, "usaha": usaha, "pro": pro}


@pytest.fixture
def free_org(organization, plans):
    organization.plan = plans["free"]
    organization.plan_paid_until = None
    organization.save()
    return organization
