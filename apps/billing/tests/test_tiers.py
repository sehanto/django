"""Starter → Growth → Pro: a trial instead of a free plan, read-only once unpaid, and what each
tier includes (tenants.domain.PlanFeature, enforced by tenants.middleware)."""

import importlib
from datetime import timedelta

import pytest
from django.apps import apps
from django.urls import reverse
from django.utils import timezone, translation

from apps.api.services import create_key
from apps.ledger.models import JournalEntry
from apps.tenants.domain import PlanFeature, SubscriptionState
from apps.tenants.models import Organization, Plan
from apps.tenants.services import create_organization

TODAY = timezone.localdate()


@pytest.fixture(autouse=True)
def english():
    with translation.override("en"):
        yield


@pytest.fixture
def on(organization, plans):
    """Put the organization on a tier, paid for a month."""
    def subscribe(code, paid_until=TODAY + timedelta(days=30)):
        organization.plan, organization.plan_paid_until, organization.trial_ends_on = plans[code], paid_until, None
        organization.save()
        return organization

    return subscribe


def test_a_new_organization_tries_starter_for_a_week(user, plans, settings):
    settings.BILLING_TRIAL_DAYS = 7
    organization = create_organization(owner=user, name="UD Baru", legal_form=Organization.LegalForm.PERORANGAN)
    assert organization.plan == plans["starter"] and organization.trial_ends_on == organization.plan_paid_until == TODAY + timedelta(days=7)
    assert organization.subscription_state() == SubscriptionState.TRIAL and not organization.is_read_only
    assert organization.subscription_state(TODAY + timedelta(days=7)) == SubscriptionState.TRIAL
    assert organization.subscription_state(TODAY + timedelta(days=8)) == SubscriptionState.LAPSED  # no grace after a trial


def test_a_paid_plan_is_read_only_after_its_grace_days(on, settings):
    settings.BILLING_GRACE_DAYS = 7
    organization = on("growth", paid_until=TODAY - timedelta(days=7))
    assert organization.subscription_state() == SubscriptionState.GRACE
    organization = on("growth", paid_until=TODAY - timedelta(days=8))
    assert organization.is_read_only and organization.plan.code == "growth"  # the plan stays, for when it pays


def journal_post():
    return {"csrfmiddlewaretoken": "t", "date": TODAY.isoformat(), "description": "Setoran modal",
            "lines-TOTAL_FORMS": "0", "lines-INITIAL_FORMS": "0"}


def test_a_read_only_organization_reads_pays_and_exports_but_records_nothing(auth_client, on, user):
    on("starter", paid_until=TODAY - timedelta(days=30))
    assert auth_client.get(reverse("ledger:journals")).status_code == 200  # reading stays
    assert auth_client.get(reverse("ledger:journal_create")).url == reverse("billing:overview")  # no form to fill in
    before = JournalEntry.objects.count()
    response = auth_client.post(reverse("ledger:journal_create"), journal_post())
    assert response.status_code == 302 and JournalEntry.objects.count() == before
    page = auth_client.get(reverse("billing:overview"))
    assert page.status_code == 200 and page.context["subscription_notice"]["state"] == SubscriptionState.LAPSED
    assert not page.context["can_write"]
    assert auth_client.post(reverse("reports:export_books")).status_code == 200  # the books can always be taken along


def test_the_api_refuses_writes_of_a_read_only_organization(client, on, user):
    organization = on("growth", paid_until=TODAY - timedelta(days=30))
    secret = create_key(organization=organization, user=user, name="Toko")[1]
    headers = {"HTTP_AUTHORIZATION": f"Bearer {secret}"}
    assert client.get(reverse("api:accounts"), **headers).status_code == 200
    response = client.post(reverse("api:journal_entries"), "{}", content_type="application/json", **headers)
    assert response.status_code == 402 and "read-only" in response.json()["error"]


@pytest.mark.parametrize(("code", "open_sections", "closed_sections"), [
    ("starter", [], ["payroll:index", "assets:list", "api_keys:list", "invoicing:coretax_connection"]),
    ("growth", ["payroll:index", "api_keys:list"], ["assets:list", "invoicing:coretax_connection"]),
    ("pro", ["payroll:index", "api_keys:list", "assets:list", "invoicing:coretax_connection"], []),
])
def test_each_tier_opens_its_features(auth_client, on, code, open_sections, closed_sections):
    on(code)
    for name in open_sections:
        assert auth_client.get(reverse(name)).status_code == 200, name
    for name in closed_sections:
        page = auth_client.get(reverse(name))
        assert page.status_code == 403 and page.context["plan"].code in ("growth", "pro"), name
    assert auth_client.get(reverse("reports:vat")).status_code == 200  # bookkeeping and tax files: every tier


def test_the_upgrade_page_names_the_cheapest_tier_with_the_feature(auth_client, on):
    on("starter")
    assert auth_client.get(reverse("payroll:index")).context["plan"].code == "growth"
    assert auth_client.get(reverse("assets:list")).context["plan"].code == "pro"


def test_the_api_needs_a_tier_that_includes_it(client, on, user):
    organization = on("starter")
    secret = create_key(organization=organization, user=user, name="Toko")[1]
    response = client.get(reverse("api:accounts"), HTTP_AUTHORIZATION=f"Bearer {secret}")
    assert response.status_code == 403 and "doesn't include the API" in response.json()["error"]


def test_the_tiers_and_their_prices():
    migration = importlib.import_module("apps.tenants.migrations.0015_plan_tiers_and_trial")
    prices = {code: (monthly, yearly) for code, _earlier, _name, monthly, yearly, *_rest in migration.TIERS}
    assert prices == {"starter": (99000, 990000), "growth": (249000, 2490000), "pro": (499000, 4990000)}


def test_existing_plans_become_tiers_and_free_organizations_start_a_trial(user, organization, db, settings):
    """tenants 0015 on the plans before it: Usaha → Starter and Pro → Growth at their prices; Gratis goes."""
    settings.BILLING_TRIAL_DAYS = 7
    Plan.objects.filter(code__in=["starter", "growth", "pro", "usaha", "gratis"]).delete()
    usaha = Plan.objects.create(code="usaha", name="Usaha", monthly_price=99000, yearly_price=990000, max_users=3)
    old_pro = Plan.objects.create(code="pro", name="Pro", monthly_price=249000, yearly_price=2490000, max_users=10)
    gratis = Plan.objects.create(code="gratis", name="Gratis", is_default=True)
    paying = create_organization(owner=user, name="CV Bayar", legal_form="cv")
    Organization.objects.filter(pk=paying.pk).update(plan=old_pro, plan_paid_until=TODAY + timedelta(days=100), trial_ends_on=None)
    Organization.objects.filter(pk=organization.pk).update(plan=gratis, plan_paid_until=None, trial_ends_on=None)
    importlib.import_module("apps.tenants.migrations.0015_plan_tiers_and_trial").tiers(apps, None)
    usaha.refresh_from_db(), old_pro.refresh_from_db(), paying.refresh_from_db(), organization.refresh_from_db()
    assert (usaha.code, usaha.monthly_price, usaha.is_default) == ("starter", 99000, True)
    assert (old_pro.code, old_pro.name, old_pro.monthly_price) == ("growth", "Growth", 249000)
    assert paying.plan == old_pro and paying.plan_paid_until == TODAY + timedelta(days=100)  # same plan, same price, same days
    new_pro = Plan.objects.get(code="pro")
    assert new_pro.monthly_price == 499000 and set(new_pro.features) == set(PlanFeature.values)
    assert organization.plan == usaha and organization.trial_ends_on == organization.plan_paid_until == TODAY + timedelta(days=7)
    assert not Plan.objects.filter(code="gratis").exists()


def test_the_tiers_allow_one_three_and_five_users(plans, organization, user, make_member):
    """Starter 1 user, Growth 3, Pro 5 (tenants 0016): inviting one more is refused, and so is a switch
    to a tier with fewer users than the organization has."""
    from apps.core.exceptions import PlanLimitExceeded
    from apps.tenants import services as tenant_services
    from apps.tenants.roles import Role

    migration = importlib.import_module("apps.tenants.migrations.0016_tier_user_limits")
    Plan.objects.filter(code__in=migration.USERS).update(max_users=None)
    migration.set_limits(apps, migration.USERS)
    limits = dict(Plan.objects.filter(code__in=migration.USERS).values_list("code", "max_users"))
    assert limits == {"starter": 1, "growth": 3, "pro": 5} == {code: plans[code].max_users for code in limits}

    organization.plan = plans["starter"]
    organization.save()
    with pytest.raises(PlanLimitExceeded, match="1 user"):  # the owner is the one user
        tenant_services.invite_member(organization=organization, email="kasir@example.com", role=Role.ACCOUNTANT, invited_by=user)
    organization.plan = plans["growth"]
    organization.save()
    tenant_services.invite_member(organization=organization, email="kasir@example.com", role=Role.ACCOUNTANT, invited_by=user)
    make_member(Role.ACCOUNTANT, email="a@example.com")
    with pytest.raises(PlanLimitExceeded):  # two members: Starter can't hold them
        tenant_services.ensure_usage_fits(organization=organization, plan=plans["starter"])
