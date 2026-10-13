"""Inviting someone without an account by email."""

import re

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.urls import reverse
from django.utils import timezone, translation

from apps.audit.models import AuditEvent
from apps.tenants.models import Invitation, Membership, Plan
from apps.tenants.roles import Role

User = get_user_model()


@pytest.fixture(autouse=True)
def english():
    with translation.override("en"):
        yield


@pytest.fixture
def roomy(organization):
    organization.plan, _ = Plan.objects.get_or_create(code="pro", defaults={"name": "Pro"})
    organization.save()
    return organization


def invite(client, email="baru@example.com", role=Role.ACCOUNTANT):
    with translation.override("en"):
        return client.post(reverse("tenants:members"), {"email": email, "role": role})


def link_in(message) -> str:
    return re.search(r"https?://\S+/organization/invitations/\S+/", message.body).group(0)


def test_someone_without_an_account_is_invited_and_joins(auth_client, client, roomy):
    invite(auth_client)
    invitation = Invitation.objects.get(organization=roomy)
    assert invitation.is_pending and len(invitation.token_hash) == 64
    [message] = mail.outbox
    url = link_in(message)
    assert message.to == ["baru@example.com"] and invitation.token_hash not in url
    assert AuditEvent.objects.filter(action="member_invited").exists()
    assert invitation in auth_client.get(reverse("tenants:members")).context["invitations"]

    newcomer = User.objects.create_user(email="Baru@example.com", password="Rahasia-123", full_name="Baru", email_verified_at=timezone.now())
    client.force_login(newcomer)
    path = url.split("://", 1)[1].split("/", 1)[1]
    assert client.get("/" + path).context["invitation"] == invitation
    with translation.override("en"):
        assert client.post("/" + path).status_code == 302
    assert Membership.objects.get(organization=roomy, user=newcomer).role == Role.ACCOUNTANT
    invitation.refresh_from_db()
    assert invitation.accepted_by == newcomer and not invitation.is_pending
    assert client.get("/" + path).context["invitation"] is None  # used once


def test_only_the_invited_address_can_accept(auth_client, client, roomy):
    invite(auth_client)
    url = link_in(mail.outbox[0])
    other = User.objects.create_user(email="lain@example.com", password="Rahasia-123", full_name="Lain", email_verified_at=timezone.now())
    client.force_login(other)
    with translation.override("en"):
        client.post("/" + url.split("://", 1)[1].split("/", 1)[1])
    assert not Membership.objects.filter(organization=roomy, user=other).exists()


def test_an_invitation_can_be_revoked_and_a_new_one_replaces_it(auth_client, roomy):
    invite(auth_client)
    invite(auth_client, role=Role.VIEWER)
    first, second = Invitation.objects.filter(organization=roomy).order_by("id")
    assert first.revoked_at is not None and second.is_pending
    with translation.override("en"):
        auth_client.post(reverse("tenants:revoke_invitation", args=[second.pk]))
    second.refresh_from_db()
    assert not second.is_pending


def test_a_registered_user_is_added_at_once(auth_client, roomy):
    known = User.objects.create_user(email="kenal@example.com", password="Rahasia-123", full_name="Kenal", email_verified_at=timezone.now())
    invite(auth_client, email="kenal@example.com")
    assert Membership.objects.filter(organization=roomy, user=known).exists()
    assert not mail.outbox and not Invitation.objects.exists()
