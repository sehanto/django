from decimal import Decimal

import pytest
from django.core.cache import cache

from apps.tenants.domain import PlanFeature
from apps.tenants.models import Plan


@pytest.fixture(autouse=True)
def _fresh_fake_gateway():
    cache.clear()  # the fake gateway keeps its state in the cache


@pytest.fixture
def plans(db):
    """The tiers as the seed migration sets them (transactional tests flush it), and a plan without a price."""
    free, _ = Plan.objects.update_or_create(code="test-free", defaults={"name": "Gratis", "max_users": 1, "max_invoices_per_month": 30})
    tiers = {}
    for code, name, monthly, users, invoices, features, order in (
        ("starter", "Starter", 99000, 1, 300, [], 1),
        ("growth", "Growth", 249000, 3, None, [PlanFeature.PAYROLL, PlanFeature.API], 2),
        ("pro", "Pro", 499000, 5, None, PlanFeature.values, 3),
    ):
        tiers[code], _ = Plan.objects.update_or_create(code=code, defaults={
            "name": name, "monthly_price": Decimal(monthly), "yearly_price": Decimal(monthly * 10), "max_users": users,
            "max_invoices_per_month": invoices, "features": features, "sort_order": order, "is_default": code == "starter",
        })
    return {"free": free, **tiers}


@pytest.fixture
def free_org(organization, plans):
    organization.plan = plans["free"]
    organization.plan_paid_until = None
    organization.save()
    return organization
