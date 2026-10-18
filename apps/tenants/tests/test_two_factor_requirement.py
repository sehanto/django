"""Organization setting: require two-factor authentication for every member."""

import time
from datetime import timedelta
from unittest import mock

import pytest
from django.core import mail
from django.urls import reverse
from django.utils import timezone
from django_otp.oath import TOTP

from apps.accounts.captcha import FAKE_PASSING_TOKEN, RESPONSE_FIELD
from apps.accounts import two_factor
from apps.accounts.models import AuthenticatorDevice
from apps.tenants.roles import Role
from apps.tenants.services import create_organization

pytestmark = pytest.mark.usefixtures("still_clock")  # authenticator codes: see conftest

SETTINGS = reverse("tenants:settings")
SECURITY = reverse("accounts:security")
DASHBOARD = reverse("dashboard:home")
PASSWORD = "Rahasia-123"


def code_for(device, steps_ahead=0) -> str:
    totp = TOTP(device.bin_key, device.step, device.t0, device.digits, device.drift)
    totp.time = time.time() + steps_ahead * device.step
    return f"{totp.token():0{device.digits}d}"


def enable_2fa(user) -> AuthenticatorDevice:
    device = two_factor.start_setup(user)
    two_factor.confirm_setup(user, device, code_for(device))
    return AuthenticatorDevice.objects.get(pk=device.pk)


def settings_post(organization, **overrides):
    data = {
        "name": organization.name, "legal_form": organization.legal_form, "npwp": "", "nib": "", "address": "",
        "city": "", "phone": "", "email": "", "uses_pph_final": "on", "lock_date": "",
        "inventory_cost_formula": organization.inventory_cost_formula, "depreciation_method": organization.depreciation_method,
        **overrides,
    }
    return {k: v for k, v in data.items() if v is not False}


def require(organization):
    organization.require_two_factor = True
    organization.save(update_fields=["require_two_factor"])


def test_owner_needs_own_2fa_before_requiring_it(auth_client, organization):
    response = auth_client.post(SETTINGS, settings_post(organization, require_two_factor="on"))
    assert "require_two_factor" in response.context["form"].errors
    organization.refresh_from_db()
    assert not organization.require_two_factor


def test_requiring_2fa_notifies_members_without_it(client, user, organization, make_member, django_user_model):
    owner_device = enable_2fa(user)
    without = make_member(Role.ACCOUNTANT, email="akuntan@example.com")
    without.language = "en"
    without.save()
    with_2fa = make_member(Role.VIEWER, email="viewer@example.com")
    enable_2fa(with_2fa)

    client.post(reverse("accounts:login"), {"username": user.email, "password": PASSWORD})
    client.post(reverse("accounts:login_otp"), {RESPONSE_FIELD: FAKE_PASSING_TOKEN, "code": code_for(owner_device, steps_ahead=1)})
    mail.outbox.clear()
    response = client.post(SETTINGS, settings_post(organization, require_two_factor="on"))
    assert response.status_code == 302
    organization.refresh_from_db()
    assert organization.require_two_factor
    assert [m.to for m in mail.outbox] == [[without.email]]  # not the owner, not members who already use 2FA
    assert mail.outbox[0].subject == f"{organization.name} now requires two-factor authentication"


def test_member_without_2fa_is_sent_to_setup(client, organization, make_member):
    require(organization)
    member = make_member(Role.ACCOUNTANT)
    client.force_login(member)
    for url in [DASHBOARD, reverse("contacts:list"), reverse("invoicing:list", kwargs={"kind": "sales"})]:
        assert client.get(url)["Location"] == SECURITY
    page = client.get(SECURITY)
    assert organization.name in page.content.decode()
    assert client.get(reverse("accounts:two_factor_setup")).status_code == 200

    device = two_factor.start_setup(member)
    client.post(reverse("accounts:two_factor_setup"), {"password": PASSWORD, "code": code_for(device), RESPONSE_FIELD: FAKE_PASSING_TOKEN})
    assert client.get(DASHBOARD).status_code == 200  # setting it up also verifies this session


def test_older_session_must_prove_the_second_factor(client, organization, make_member):
    require(organization)
    member = make_member(Role.ACCOUNTANT)
    device = enable_2fa(member)
    client.force_login(member)  # e.g. signed in before turning 2FA on
    response = client.get(reverse("contacts:list") + "?q=kopi")
    step_up = reverse("accounts:two_factor_verify_session")
    assert response["Location"].startswith(step_up) and "next=%2Fcontacts%2F%3Fq%3Dkopi" in response["Location"]

    wrong = client.post(step_up, {"code": "000000", "next": "/contacts/?q=kopi", RESPONSE_FIELD: FAKE_PASSING_TOKEN})
    assert wrong.status_code == 200
    with mock.patch("django_otp.models.timezone.now", return_value=timezone.now() + timedelta(minutes=1)):
        ok = client.post(step_up, {"code": code_for(device, steps_ahead=1), "next": "/contacts/?q=kopi", RESPONSE_FIELD: FAKE_PASSING_TOKEN})
    assert ok["Location"] == "/contacts/?q=kopi"
    assert client.get(reverse("contacts:list")).status_code == 200


def test_step_up_ignores_foreign_next_urls(client, organization, make_member):
    require(organization)
    member = make_member(Role.ACCOUNTANT)
    device = enable_2fa(member)
    client.force_login(member)
    response = client.post(reverse("accounts:two_factor_verify_session"), {"code": code_for(device, steps_ahead=1), "next": "https://evil.example/", RESPONSE_FIELD: FAKE_PASSING_TOKEN})
    assert response["Location"] == DASHBOARD


def test_members_can_still_switch_to_another_organization(client, organization, make_member):
    require(organization)
    member = make_member(Role.ACCOUNTANT)
    own = create_organization(owner=member, name="Usaha Sendiri", legal_form="perorangan")
    client.force_login(member)
    client.get(DASHBOARD)  # lands on the required organization first
    client.post(reverse("tenants:switch", args=[own.pk]))
    assert client.get(DASHBOARD).status_code == 200


def test_cannot_turn_off_2fa_while_required(client, user, organization):
    device = enable_2fa(user)
    require(organization)
    client.post(reverse("accounts:login"), {"username": user.email, "password": PASSWORD})
    client.post(reverse("accounts:login_otp"), {RESPONSE_FIELD: FAKE_PASSING_TOKEN, "code": code_for(device, steps_ahead=1)})
    page = client.get(reverse("accounts:two_factor_disable"))
    assert page.context["required_by"] == [organization]
    response = client.post(reverse("accounts:two_factor_disable"), {"password": PASSWORD, "code": code_for(device, steps_ahead=2), RESPONSE_FIELD: FAKE_PASSING_TOKEN})
    assert response.context["form"].non_field_errors()
    assert two_factor.is_enabled(user)


def test_members_page_shows_2fa_status(auth_client, user, organization, make_member):
    enable_2fa(user)
    make_member(Role.VIEWER)
    members = {m.user.email: m.has_two_factor for m in auth_client.get(reverse("tenants:members")).context["members"]}
    assert members == {user.email: True, "member@example.com": False}


def test_organizations_without_the_rule_are_unaffected(client, organization, make_member):
    member = make_member(Role.ACCOUNTANT)
    client.force_login(member)
    assert client.get(DASHBOARD).status_code == 200
