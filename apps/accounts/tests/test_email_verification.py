"""Mandatory email verification after sign-up."""

import re

import pytest
from django.conf import settings
from django.core import mail, signing
from django.core.cache import cache
from django.urls import reverse

from apps.accounts.captcha import FAKE_PASSING_TOKEN, RESPONSE_FIELD
from apps.accounts import services
from apps.accounts.models import User

PENDING = reverse("accounts:verify_email_pending")
SIGNUP = reverse("accounts:signup")
PASSWORD = "Kuat-Sekali-99"


@pytest.fixture(autouse=True)
def _fresh_throttles():
    cache.clear()


@pytest.fixture
def site(settings, db):
    settings.SITE_URL = "https://app.ea-system.id"
    return settings


def sign_up(client, email="dewi@example.com", **extra):
    return client.post(SIGNUP, {
        "full_name": "Dewi Lestari", "email": email, "password": PASSWORD,
        "organization_name": "Batik Dewi", "legal_form": "perorangan", RESPONSE_FIELD: FAKE_PASSING_TOKEN, **extra,
    })


def link_in(message) -> str:
    return re.search(r"https://app\.ea-system\.id(/account/verify-email/\S+/)", message.body).group(1)


def test_new_account_is_gated_until_confirmed(client, site):
    assert sign_up(client)["Location"] == PENDING
    user = User.objects.get(email="dewi@example.com")
    assert not user.is_email_verified
    for url in [reverse("dashboard:home"), reverse("contacts:list"), reverse("invoicing:list", kwargs={"kind": "sales"})]:
        assert client.get(url)["Location"] == PENDING
    assert client.post(reverse("contacts:create"), {"name": "x"})["Location"] == PENDING  # writes blocked too
    assert client.get(PENDING).status_code == 200

    message = mail.outbox[0]
    assert message.to == ["dewi@example.com"]
    assert message.subject == "Konfirmasi email Anda untuk EA-System"
    confirmed = client.get(link_in(message), follow=True)
    assert confirmed.redirect_chain[-1][0] == reverse("tenants:settings")
    user.refresh_from_db()
    assert user.is_email_verified
    assert client.get(reverse("dashboard:home")).status_code == 200


def test_email_follows_the_users_language(client, site):
    client.post(reverse("accounts:set_language"), {"language": "en", "next": "/"})
    sign_up(client)
    assert mail.outbox[0].subject == "Confirm your email for EA-System"
    assert "valid for 3\xa0days" in mail.outbox[0].body


def test_link_works_when_signed_out(client, site):
    sign_up(client)
    client.logout()
    response = client.get(link_in(mail.outbox[0]))
    assert response["Location"] == reverse("accounts:login")
    assert User.objects.get(email="dewi@example.com").is_email_verified


@pytest.mark.parametrize("tamper", ["forged", "expired", "email-changed"])
def test_invalid_links_are_refused(client, site, tamper):
    sign_up(client)
    user = User.objects.get(email="dewi@example.com")
    link = link_in(mail.outbox[0])
    if tamper == "forged":
        link = link.replace(link.split("/")[-2], signing.dumps({"user": user.pk, "email": user.email}, salt="other"))
    elif tamper == "expired":
        settings.EMAIL_VERIFICATION_TIMEOUT = -1
    else:
        user.email = "changed@example.com"
        user.save()
    response = client.get(link)
    assert response["Location"] == PENDING
    user.refresh_from_db()
    assert not user.is_email_verified


def test_resend_is_throttled(client, site):
    sign_up(client)  # first email
    for _ in range(settings.EMAIL_VERIFICATION_EMAILS_PER_HOUR + 1):
        client.post(reverse("accounts:verify_email_resend"), {RESPONSE_FIELD: FAKE_PASSING_TOKEN})
    assert len(mail.outbox) == settings.EMAIL_VERIFICATION_EMAILS_PER_HOUR


def test_mistyped_address_can_be_corrected(client, site, django_capture_on_commit_callbacks):
    sign_up(client, email="dewi@exmaple.com")
    wrong_password = client.post(reverse("accounts:verify_email_change"), {"email": "dewi@example.com", "password": "nope", RESPONSE_FIELD: FAKE_PASSING_TOKEN})
    assert wrong_password.status_code == 200 and wrong_password.context["change_form"].errors["password"]

    with django_capture_on_commit_callbacks(execute=True):  # the email is sent after the change commits
        client.post(reverse("accounts:verify_email_change"), {"email": " Dewi@Example.com ", "password": PASSWORD, RESPONSE_FIELD: FAKE_PASSING_TOKEN})
    user = User.objects.get(pk=User.objects.get(email="dewi@example.com").pk)
    assert mail.outbox[-1].to == ["dewi@example.com"]
    old_link, new_link = link_in(mail.outbox[0]), link_in(mail.outbox[-1])
    assert client.get(old_link)["Location"] == PENDING  # the link for the typo is dead
    client.get(new_link)
    user.refresh_from_db()
    assert user.is_email_verified


def test_cannot_switch_to_an_address_already_registered(client, site, user):
    sign_up(client)
    response = client.post(reverse("accounts:verify_email_change"), {"email": user.email, "password": PASSWORD, RESPONSE_FIELD: FAKE_PASSING_TOKEN})
    assert response.context["change_form"].errors["email"]


def test_password_reset_also_confirms_the_address(client, site):
    sign_up(client)
    client.logout()
    client.post(reverse("accounts:password_reset"), {"email": "dewi@example.com", RESPONSE_FIELD: FAKE_PASSING_TOKEN})
    reset_path = re.search(r"https://app\.ea-system\.id(/account/password-reset/\S+/)", mail.outbox[-1].body).group(1)
    form = client.get(reset_path, follow=True)
    client.post(form.redirect_chain[-1][0], {"new_password1": "Baru-Sekali-456", "new_password2": "Baru-Sekali-456"})
    assert User.objects.get(email="dewi@example.com").is_email_verified


def test_admin_and_superusers_are_not_gated(client, db):
    admin = User.objects.create_superuser(email="root@example.com", password=PASSWORD, full_name="Root")
    assert admin.is_email_verified
    staff = User.objects.create_user(email="staff@example.com", password=PASSWORD, full_name="Staff", is_staff=True)
    client.force_login(staff)
    assert client.get(reverse("admin:index")).status_code in (200, 302)
    assert client.get(reverse("admin:index")).get("Location", "") != PENDING


def test_verified_users_skip_the_pending_page(auth_client):
    assert auth_client.get(PENDING)["Location"] == reverse("dashboard:home")
