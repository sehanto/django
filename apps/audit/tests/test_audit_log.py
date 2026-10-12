from datetime import date, timedelta
from unittest import mock

import pytest
from axes.models import AccessLog
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
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
                   {"details": {"forged": True}}, {"ip_address": "198.51.100.66"}, {"user_agent": "forged"}):
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


def test_retention_keeps_organization_history_longer(user, organization):
    assert (settings.SECURITY_LOG_RETENTION_DAYS, settings.ORGANIZATION_LOG_RETENTION_DAYS) == (90, 3650)
    days_ago = lambda days: timezone.now() - timedelta(days=days)  # noqa: E731

    def event(action, days):
        return AuditEvent.objects.create(action=action, actor=user, organization=organization, created_at=days_ago(days)).pk

    expired = {event(A.LOGIN_SUCCEEDED, 91), event(A.TWO_FACTOR_DISABLED, 91), event(A.MEMBER_ADDED, 3651),
               event(A.PLAN_CHANGED, 3651), event(A.ENCRYPTION_KEYS_ROTATED, 3651)}
    kept = {event(A.LOGIN_SUCCEEDED, 89), event(A.MEMBER_ADDED, 91), event(A.ORGANIZATION_SETTINGS_CHANGED, 3649),
            event(A.PAYMENT_CONFIRMED_MANUALLY, 400), event(A.ENCRYPTION_KEYS_ROTATED, 91)}
    old_sign_in = AccessLog.objects.create(username=user.email, ip_address="203.0.113.5", user_agent="x")
    kept_sign_in = AccessLog.objects.create(username=user.email, ip_address="203.0.113.6", user_agent="x")
    AccessLog.objects.filter(pk=old_sign_in.pk).update(attempt_time=days_ago(91))
    AccessLog.objects.filter(pk=kept_sign_in.pk).update(attempt_time=days_ago(89))

    call_command("prune_security_logs")  # default periods; tests connect as the owner, who may prune

    remaining = set(AuditEvent.objects.filter(pk__in=expired | kept).values_list("pk", flat=True))
    assert remaining == kept
    assert set(AccessLog.objects.values_list("pk", flat=True)) == {kept_sign_in.pk}


def test_network_details_of_kept_events_are_blanked_after_90_days(user, organization):
    def event(action, days):
        return AuditEvent.objects.create(
            action=action, actor=user, actor_email=user.email, organization=organization, target="kasir@example.com",
            ip_address="203.0.113.9", user_agent="Firefox/140", created_at=timezone.now() - timedelta(days=days),
        )

    old_member, recent_member, recent_sign_in = event(A.MEMBER_ADDED, 91), event(A.MEMBER_ADDED, 89), event(A.LOGIN_SUCCEEDED, 89)
    call_command("prune_security_logs")

    for kept in (old_member, recent_member, recent_sign_in):
        kept.refresh_from_db()
    assert (old_member.ip_address, old_member.user_agent) == (None, "")
    assert (old_member.actor, old_member.actor_email, old_member.target) == (user, user.email, "kasir@example.com")
    assert recent_member.ip_address == recent_sign_in.ip_address == "203.0.113.9"
    assert recent_member.user_agent == "Firefox/140"


def test_pruning_needs_the_owner(user):
    with mock.patch("apps.audit.management.commands.prune_security_logs.connected_role_can_alter_schema", return_value=False):
        with pytest.raises(CommandError, match="DATABASE_OWNER_URL"):
            call_command("prune_security_logs")


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
