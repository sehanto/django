"""Password reset by email: Django's token flow plus our hardening."""

import re

import pytest
from axes.models import AccessAttempt
from django.conf import settings
from django.core import mail
from django.core.cache import cache
from django.urls import reverse

from apps.accounts.captcha import FAKE_PASSING_TOKEN, RESPONSE_FIELD

RESET = reverse("accounts:password_reset")
SENT = reverse("accounts:password_reset_done")
OLD_PASSWORD = "Rahasia-123"  # from the ``user`` fixture
NEW_PASSWORD = "Baru-Sekali-456"


@pytest.fixture(autouse=True)
def _fresh_throttles():
    cache.clear()


@pytest.fixture
def site(settings):
    settings.SITE_URL = "https://app.ea-system.id"
    return settings


def request_reset(client, email, ip="203.0.113.10", **headers):
    return client.post(RESET, {"email": email, RESPONSE_FIELD: FAKE_PASSING_TOKEN}, REMOTE_ADDR=ip, **headers)


def reset_link(message) -> str:
    return re.search(r"https?://\S+/account/password-reset/\S+/", message.body).group(0)


def path_of(link: str) -> str:
    return link.split("app.ea-system.id", 1)[1]


def set_new_password(client, link, password=NEW_PASSWORD):
    form_page = client.get(path_of(link), follow=True)  # Django swaps the token for a session marker
    assert form_page.context["validlink"]
    return client.post(form_page.redirect_chain[-1][0], {"new_password1": password, "new_password2": password})


def test_full_reset_flow(client, user, site):
    response = request_reset(client, "  OWNER@Example.com ")  # sanitized and case-insensitive
    assert response["Location"] == SENT
    assert len(mail.outbox) == 1
    message = mail.outbox[0]
    assert message.to == [user.email]
    link = reset_link(message)
    assert link.startswith("https://app.ea-system.id/account/password-reset/")

    done = set_new_password(client, link)
    assert done["Location"] == reverse("accounts:password_reset_complete")
    user.refresh_from_db()
    assert user.check_password(NEW_PASSWORD) and not user.check_password(OLD_PASSWORD)
    assert client.post(reverse("accounts:login"), {"username": user.email, "password": NEW_PASSWORD}).status_code == 302


def test_email_is_in_the_account_holders_language(client, user, site):
    user.language = "en"
    user.save()
    request_reset(client, user.email)  # the visitor browses in Indonesian (default)
    message = mail.outbox[0]
    assert message.subject == "Reset your EA-System password"
    assert "expires in 1\xa0hour" in message.body


def test_indonesian_email_by_default(client, user, site):
    request_reset(client, user.email)
    assert mail.outbox[0].subject == "Atur ulang kata sandi EA-System Anda"


def test_unknown_address_looks_identical_and_sends_nothing(client, db, site):
    response = request_reset(client, "nobody@example.com")
    assert response["Location"] == SENT
    assert mail.outbox == []


def test_link_is_single_use(client, user, site):
    request_reset(client, user.email)
    link = reset_link(mail.outbox[0])
    set_new_password(client, link)
    reused = client.get(path_of(link), follow=True)
    assert not reused.context["validlink"]


def test_link_expires(client, user, site, settings):
    request_reset(client, user.email)
    link = reset_link(mail.outbox[0])
    settings.PASSWORD_RESET_TIMEOUT = -1  # already expired
    assert not client.get(path_of(link), follow=True).context["validlink"]


def test_reset_requests_are_throttled_without_revealing_it(client, user, site):
    limit = settings.PASSWORD_RESET_EMAILS_PER_ADDRESS_PER_HOUR
    for i in range(limit + 2):
        response = request_reset(client, user.email, ip=f"198.51.100.{i}")  # different IPs, same address
        assert response["Location"] == SENT  # same page every time
    assert len(mail.outbox) == limit


def test_one_ip_cannot_spray_many_addresses(client, django_user_model, site):
    limit = settings.PASSWORD_RESET_REQUESTS_PER_IP_PER_HOUR
    for i in range(limit + 3):
        django_user_model.objects.create_user(email=f"user{i}@example.com", password=OLD_PASSWORD, full_name=f"U{i}")
        request_reset(client, f"user{i}@example.com")
    assert len(mail.outbox) == limit


def test_link_ignores_a_forged_host_header(client, user, site, settings):
    settings.ALLOWED_HOSTS = ["*"]  # even if the Host header were accepted...
    request_reset(client, user.email, HTTP_HOST="evil.example")
    link = reset_link(mail.outbox[0])
    assert "evil.example" not in link and link.startswith("https://app.ea-system.id/")  # ...the link uses SITE_URL


def test_reset_lifts_a_login_lockout(client, user, site):
    for _ in range(settings.AXES_FAILURE_LIMIT):
        client.post(reverse("accounts:login"), {"username": user.email, "password": "wrong", RESPONSE_FIELD: FAKE_PASSING_TOKEN}, REMOTE_ADDR="203.0.113.10")
    assert AccessAttempt.objects.exists()
    request_reset(client, user.email)
    set_new_password(client, reset_link(mail.outbox[0]))
    assert not AccessAttempt.objects.exists()
    # The address itself still has recent failures, so the CAPTCHA stays on for it.
    login = client.post(reverse("accounts:login"), {"username": user.email, "password": NEW_PASSWORD,
                                                    RESPONSE_FIELD: FAKE_PASSING_TOKEN}, REMOTE_ADDR="203.0.113.10")
    assert login.status_code == 302


def test_reset_signs_out_other_sessions(client, user, site, django_user_model):
    from django.test import Client

    elsewhere = Client()
    elsewhere.force_login(user)
    assert elsewhere.get(reverse("accounts:profile")).status_code == 200
    request_reset(client, user.email)
    set_new_password(client, reset_link(mail.outbox[0]))
    assert elsewhere.get(reverse("accounts:profile")).status_code == 302  # session no longer valid


def test_weak_new_password_rejected(client, user, site):
    request_reset(client, user.email)
    response = set_new_password(client, reset_link(mail.outbox[0]), password="12345678")
    assert response.status_code == 200 and response.context["form"].errors


def test_inactive_accounts_get_no_email(client, user, site):
    user.is_active = False
    user.save()
    request_reset(client, user.email)
    assert mail.outbox == []


def test_login_and_lockout_pages_link_to_reset(client, user):
    assert RESET in client.get(reverse("accounts:login")).content.decode()
    for _ in range(settings.AXES_FAILURE_LIMIT):
        locked = client.post(reverse("accounts:login"), {"username": user.email, "password": "wrong", RESPONSE_FIELD: FAKE_PASSING_TOKEN})
    assert locked.status_code == 429 and RESET in locked.content.decode()
