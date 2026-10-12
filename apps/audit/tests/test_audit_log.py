from datetime import date, timedelta

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from apps.accounts import two_factor
from apps.accounts.tests.test_two_factor import OTP, code_for, later
from apps.audit import services as audit
from apps.audit.domain import ACTION_CATEGORY, AuditAction, AuditCategory
from apps.audit.models import AuditEvent
from apps.tenants.models import Plan
from apps.tenants.roles import Role
from apps.tenants.services import set_subscription

LOGIN = reverse("accounts:login")
PASSWORD = "Rahasia-123"  # from the ``user`` fixture
A = AuditAction


def events(action, **filters):
    return AuditEvent.objects.filter(action=action, **filters)


def everything_recorded() -> str:
    return " ".join(f"{e.target} {e.details} {e.user_agent} {e.actor_email}" for e in AuditEvent.objects.all())


def test_every_action_has_a_category():
    assert set(ACTION_CATEGORY) == set(AuditAction)


# --- Append-only ----------------------------------------------------------------------


def test_events_cannot_be_rewritten(organization, user):
    event = audit.record(A.MEMBER_ADDED, actor=user, organization=organization, target="a@example.com")
    for change in ({"target": "someone-else"}, {"action": A.LOGIN_SUCCEEDED}, {"created_at": timezone.now() - timedelta(days=9)},
                   {"details": {"forged": True}}):
        with pytest.raises(IntegrityError), transaction.atomic():
            AuditEvent.objects.filter(pk=event.pk).update(**change)
    event.refresh_from_db()
    assert event.target == "a@example.com"


def test_deleting_the_user_keeps_their_events(user):
    event = audit.record(A.PASSWORD_CHANGED, actor=user)
    get_user_model().objects.filter(pk=user.pk).delete()
    event.refresh_from_db()
    assert event.actor is None and event.actor_email == user.email


def test_organization_deletion_is_logged_and_survives(organization):
    call_command("delete_organization", str(organization.pk), "--yes", "--skip-export")
    deleted = events(A.ORGANIZATION_DELETED).get()
    assert deleted.organization is None and deleted.organization_name == organization.name
    assert events(A.ORGANIZATION_CREATED, organization_name=organization.name).exists()


def test_prune_removes_only_old_events(user):
    old = AuditEvent.objects.create(action=A.LOGIN_SUCCEEDED, actor=user, created_at=timezone.now() - timedelta(days=800))
    recent = audit.record(A.LOGIN_SUCCEEDED, actor=user)
    call_command("prune_audit_log", "--days", "730")  # tests connect as the owner, who may prune
    assert not AuditEvent.objects.filter(pk=old.pk).exists()
    assert AuditEvent.objects.filter(pk=recent.pk).exists()


# --- Sign-in --------------------------------------------------------------------------


def test_sign_ins_and_failures_are_recorded_without_the_password(client, user):
    client.post(LOGIN, {"username": user.email.upper(), "password": "Wrong-Guess-987"}, REMOTE_ADDR="203.0.113.7",
                HTTP_USER_AGENT="Firefox/140")
    failed = events(A.LOGIN_FAILED).get()
    assert failed.actor == user and failed.ip_address == "203.0.113.7" and failed.user_agent == "Firefox/140"

    client.post(LOGIN, {"username": user.email, "password": PASSWORD}, REMOTE_ADDR="203.0.113.7")
    assert events(A.LOGIN_SUCCEEDED, actor=user, ip_address="203.0.113.7").exists()
    assert "Wrong-Guess-987" not in everything_recorded() and PASSWORD not in everything_recorded()


def test_unknown_email_failures_are_recorded_without_an_actor(client, db):
    client.post(LOGIN, {"username": "nobody@example.com", "password": "x"})
    failed = events(A.LOGIN_FAILED).get()
    assert failed.actor is None and failed.target == "nobody@example.com"


def test_lockout_is_recorded(client, user):
    for _ in range(settings.AXES_FAILURE_LIMIT):
        client.post(LOGIN, {"username": user.email, "password": "wrong"})
    assert events(A.ACCOUNT_LOCKED, actor=user).exists()


def test_two_factor_events(client, user):
    device = two_factor.start_setup(user)
    backup_codes = two_factor.confirm_setup(user, device, code_for(device))
    client.post(LOGIN, {"username": user.email, "password": PASSWORD})
    client.post(OTP, {"code": "000000"})
    wrong = events(A.LOGIN_FAILED, actor=user).get()
    assert wrong.details["step"] == "two_factor"

    with later():  # django-otp's back-off after the wrong code
        client.post(OTP, {"code": backup_codes[0], "remember": "on"}, HTTP_USER_AGENT="Mozilla/5.0 (Windows NT 10.0) Firefox/140")
    assert events(A.BACKUP_CODE_USED, actor=user).exists()
    assert events(A.DEVICE_REMEMBERED, actor=user).exists()
    assert backup_codes[0] not in everything_recorded()


def test_password_change_is_recorded(auth_client, user):
    auth_client.post(reverse("accounts:password_change"), {
        "old_password": PASSWORD, "new_password1": "Baru-Rahasia-456", "new_password2": "Baru-Rahasia-456",
    })
    assert events(A.PASSWORD_CHANGED, actor=user).exists()
    assert "Baru-Rahasia-456" not in everything_recorded()


# --- Organization ---------------------------------------------------------------------


def settings_form(organization, **changes):
    data = {
        "name": organization.name, "legal_form": organization.legal_form, "npwp": "", "nib": "", "address": "",
        "city": "", "phone": "", "email": "", "uses_pph_final": "on", "lock_date": "",
    }
    return {**data, **changes}


def test_settings_changes_are_recorded_field_by_field(auth_client, organization, user):
    auth_client.post(reverse("tenants:settings"), settings_form(organization, lock_date="2026-01-31"))
    changed = events(A.ORGANIZATION_SETTINGS_CHANGED, organization=organization).get()
    assert changed.actor == user
    assert changed.details["changes"] == {"lock_date": [None, "2026-01-31"]}

    page = auth_client.get(reverse("audit:organization")).content.decode()
    assert "2026-01-31" in page


def test_member_changes_are_recorded(auth_client, organization, user, make_member):
    member = make_member(Role.VIEWER, email="kasir@example.com")
    organization.memberships.filter(user=member).delete()
    auth_client.post(reverse("tenants:members"), {"email": member.email, "role": Role.ACCOUNTANT})
    added = events(A.MEMBER_ADDED, organization=organization).get()
    assert (added.target, added.details["role"]) == (member.email, Role.ACCOUNTANT)

    membership = organization.memberships.get(user=member)
    auth_client.post(reverse("tenants:remove_member", args=[membership.pk]))
    assert events(A.MEMBER_REMOVED, organization=organization, target=member.email).exists()


def test_export_and_plan_changes_are_recorded(auth_client, organization):
    auth_client.post(reverse("reports:export_books"))
    assert events(A.BOOKS_EXPORTED, organization=organization).get().details == {"via": "web"}

    previous = organization.plan.name
    other_plan = Plan.objects.exclude(pk=organization.plan_id).first() or Plan.objects.create(
        code="usaha-test", name="Usaha Test", monthly_price=99000,
    )
    set_subscription(organization=organization, plan=other_plan, paid_until=date(2026, 12, 31))
    changed = events(A.PLAN_CHANGED, organization=organization).get()
    assert changed.target == other_plan.name and changed.details["previous_plan"] == previous


# --- Who sees what --------------------------------------------------------------------


def test_activity_page_shows_only_this_organization(auth_client, organization, other_organization):
    audit.record(A.MEMBER_ADDED, organization=organization, target="ours@example.com", role=Role.VIEWER)
    audit.record(A.MEMBER_ADDED, organization=other_organization, target="theirs@example.com", role=Role.VIEWER)
    page = auth_client.get(reverse("audit:organization")).content.decode()
    assert "ours@example.com" in page and "theirs@example.com" not in page

    filtered = auth_client.get(reverse("audit:organization"), {"category": AuditCategory.BILLING}).content.decode()
    assert "ours@example.com" not in filtered


def test_only_managers_see_the_activity_log(client, make_member):
    for role in (Role.VIEWER, Role.ACCOUNTANT):
        client.force_login(make_member(role, email=f"{role}@example.com"))
        assert client.get(reverse("audit:organization")).status_code == 403


def test_security_page_lists_only_your_own_activity(auth_client, user, make_member):
    other = make_member(Role.VIEWER, email="other@example.com")
    audit.record(A.PASSWORD_CHANGED, actor=other, target="other-marker")
    audit.record(A.DEVICES_FORGOTTEN, actor=user, count=2)
    page = auth_client.get(reverse("accounts:security")).content.decode()
    assert "other-marker" not in page
    assert "Remembered devices forgotten" in page or "Perangkat yang diingat dilupakan" in page
