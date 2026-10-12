"""Shared pytest fixtures."""

from datetime import date
from decimal import Decimal

import pytest

from apps.accounts.models import User
from apps.contacts.models import Contact
from apps.ledger.domain import SystemRole
from apps.ledger.models import Account
from apps.tenants.models import Plan
from apps.tenants.roles import Role
from apps.tenants.services import create_organization


@pytest.fixture
def user(db):
    return User.objects.create_user(email="owner@example.com", password="Rahasia-123", full_name="Budi Santoso")


@pytest.fixture
def organization(user):
    org = create_organization(owner=user, name="Toko Maju Jaya", legal_form="perorangan")
    # Unlimited plan so tests are not constrained by quotas. Created here rather than
    # relying on the seed migration, because transactional tests flush the database.
    org.plan, _ = Plan.objects.get_or_create(code="pro", defaults={"name": "Pro"})
    org.save()
    return org


@pytest.fixture
def pkp_organization(organization):
    organization.is_pkp = True
    organization.save()
    return organization


@pytest.fixture
def other_organization(db):
    other = User.objects.create_user(email="other@example.com", password="Rahasia-123", full_name="Siti")
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
def auth_client(client, user, organization):
    client.force_login(user)
    return client


@pytest.fixture
def make_member(organization):
    def make(role: Role, email="member@example.com"):
        member = User.objects.create_user(email=email, password="Rahasia-123", full_name="Anggota")
        organization.memberships.create(user=member, role=role)
        return member

    return make


D = Decimal
TODAY = date(2026, 3, 10)
