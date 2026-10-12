import pytest

from apps.accounts.models import User
from apps.core.exceptions import PlanLimitExceeded, ValidationFailed
from apps.tenants import services
from apps.tenants.models import Membership, Plan
from apps.tenants.roles import Permission, Role


def test_register_owner_creates_tenant_on_default_plan(db):
    user, org = services.register_owner_with_organization(
        email="Baru@Example.com", password="Rahasia-123", full_name="Ani", organization_name="Warung Ani", legal_form="perorangan"
    )
    assert user.email == "baru@example.com"
    assert org.plan.is_default
    assert Membership.objects.get(user=user, organization=org).role == Role.OWNER
    assert org.is_individual_taxpayer


def test_role_permissions():
    assert Role.OWNER.allows(Permission.MANAGE)
    assert Role.ACCOUNTANT.allows(Permission.WRITE)
    assert not Role.ACCOUNTANT.allows(Permission.MANAGE)
    assert Role.VIEWER.allows(Permission.VIEW)
    assert not Role.VIEWER.allows(Permission.WRITE)


def test_add_member_respects_plan_and_rules(organization, user, other_organization):
    organization.plan = Plan.objects.create(code="duo", name="Duo", max_users=2)
    organization.save()
    with pytest.raises(ValidationFailed):
        services.add_member(organization=organization, email="ghost@example.com", role=Role.VIEWER)
    with pytest.raises(ValidationFailed):
        services.add_member(organization=organization, email=user.email, role=Role.VIEWER)
    with pytest.raises(ValidationFailed):
        services.add_member(organization=organization, email="other@example.com", role=Role.OWNER)

    services.add_member(organization=organization, email="other@example.com", role=Role.ACCOUNTANT)

    User.objects.create_user(email="third@example.com", password="x", full_name="Ketiga")
    with pytest.raises(PlanLimitExceeded):
        services.add_member(organization=organization, email="third@example.com", role=Role.VIEWER)
    with pytest.raises(PlanLimitExceeded):
        services.ensure_usage_fits(organization=organization, plan=Plan.objects.create(code="solo", name="Solo", max_users=1))


def test_owner_cannot_be_removed(organization, user, make_member):
    owner = organization.memberships.get(user=user)
    member = make_member(Role.VIEWER)
    with pytest.raises(ValidationFailed):
        services.remove_member(organization=organization, membership_id=owner.pk, acting_user=member)
    membership = organization.memberships.get(user=member)
    services.remove_member(organization=organization, membership_id=membership.pk, acting_user=user)
    assert not organization.memberships.filter(user=member).exists()


def test_delete_organization_removes_all_tenant_data(organization, other_organization, customer, account):
    from datetime import date
    from decimal import Decimal

    from apps.invoicing import services as invoicing
    from apps.invoicing.services import InvoiceLineInput
    from apps.ledger.models import Account, JournalEntry
    from apps.ledger.services import reverse_journal_entry

    invoice = invoicing.create_invoice(
        organization=organization, kind="sales", contact=customer, date=date(2026, 1, 5),
        lines=[InvoiceLineInput("x", account("4-1000"), Decimal("1"), Decimal("1000"))],
    )
    invoice = invoicing.issue_invoice(invoice=invoice)
    invoicing.record_payment(invoice=invoice, date=date(2026, 1, 6), amount=Decimal("1000"), cash_account=account("1-1000"))
    reverse_journal_entry(entry=invoice.journal_entry, date=date(2026, 1, 7))

    services.delete_organization(organization=organization)

    assert not Account.objects.filter(organization_id=organization.pk).exists()
    assert not JournalEntry.objects.filter(organization_id=organization.pk).exists()
    assert Account.objects.filter(organization=other_organization).exists()
