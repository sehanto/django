"""Shared pytest fixtures."""

import time
from datetime import date
from decimal import Decimal

import pytest
from django.core.cache import cache
from django.utils import timezone

from apps.accounts.models import User
from apps.contacts.models import Contact
from apps.ledger.domain import SystemRole
from apps.ledger.models import Account
from apps.tenants.domain import PlanFeature
from apps.tenants.models import Plan
from apps.tenants.roles import Role
from apps.tenants.services import create_organization


@pytest.fixture
def still_clock(monkeypatch):
    """Authenticator codes follow time.time() in 30-second steps. Held still, a step can't end in the
    middle of a test, where the codes a test makes and the server's drift estimate would disagree."""
    now = time.time()
    monkeypatch.setattr(time, "time", lambda: now)


@pytest.fixture(autouse=True)
def fresh_cache():
    """Throttle and sign-in failure counters live in the cache; never carry them across tests."""
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def user(db):
    return User.objects.create_user(
        email="owner@example.com", password="Rahasia-123", full_name="Budi Santoso", email_verified_at=timezone.now()
    )


@pytest.fixture
def organization(user):
    org = create_organization(owner=user, name="Toko Maju Jaya", legal_form="perorangan")
    # A plan with every feature, no limits and no price (so never read-only), for tests that aren't
    # about plans. Created here rather than relying on the seed migration, because transactional
    # tests flush the database. Billing tests use the real tiers (apps/billing/tests/conftest.py).
    org.plan, _ = Plan.objects.update_or_create(code="test-all", defaults={"name": "Semua fitur", "features": PlanFeature.values})
    org.save()
    return org


@pytest.fixture
def pkp_organization(organization):
    organization.is_pkp = True
    organization.save()
    return organization


@pytest.fixture
def other_organization(db):
    other = User.objects.create_user(email="other@example.com", password="Rahasia-123", full_name="Siti", email_verified_at=timezone.now())
    return create_organization(owner=other, name="CV Lain", legal_form="cv")


@pytest.fixture
def account(organization):
    def get(code: str) -> Account:
        return Account.objects.get(organization=organization, code=code)

    return get


@pytest.fixture
def system_account(organization):
    def get(role: SystemRole) -> Account:
        return Account.objects.get(organization=organization, system_role=role)

    return get


@pytest.fixture
def customer(organization):
    return Contact.objects.create(organization=organization, kind=Contact.Kind.CUSTOMER, name="PT Pelanggan", payment_terms_days=14)


@pytest.fixture
def supplier(organization):
    return Contact.objects.create(organization=organization, kind=Contact.Kind.SUPPLIER, name="UD Pemasok")


@pytest.fixture
def regions(db):
    """``regions("36.03.22.2008", ...)`` loads those kelurahan/desa (and the levels above them)
    from the real Kemendagri list and returns them by code."""
    from apps.regions.models import Village
    from apps.regions.services import load_regions, read_rows

    rows, wanted = read_rows(), set()

    def load(*codes):
        wanted.update(code.rsplit(".", depth)[0] for code in codes for depth in range(4))  # with its district, regency, province
        load_regions([row for row in rows if row[0] in wanted])
        return {v.code: v for v in Village.objects.filter(code__in=codes).select_related("district__regency__province")}

    return load


@pytest.fixture
def jatake(regions):
    """Jatake, Pagedangan, Kabupaten Tangerang, Banten 15330."""
    return regions("36.03.22.2008")["36.03.22.2008"]


@pytest.fixture
def auth_client(client, user, organization):
    client.force_login(user)
    return client


@pytest.fixture
def make_member(organization):
    def make(role: Role, email="member@example.com"):
        member = User.objects.create_user(email=email, password="Rahasia-123", full_name="Anggota", email_verified_at=timezone.now())
        organization.memberships.create(user=member, role=role)
        return member

    return make


D = Decimal
TODAY = date(2026, 3, 10)
