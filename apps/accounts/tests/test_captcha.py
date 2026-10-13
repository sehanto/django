"""Login CAPTCHA (Cloudflare Turnstile): invisible to normal users, required after repeated failures."""

from unittest import mock

import pytest
import requests
from axes.models import AccessAttempt
from django.conf import settings
from django.test import RequestFactory, override_settings
from django.urls import reverse

from apps.accounts import captcha
from apps.accounts.captcha import FAKE_PASSING_TOKEN, RESPONSE_FIELD
from apps.core.checks import check_captcha_keys

LOGIN = reverse("accounts:login")
PASSWORD = "Rahasia-123"  # from the ``user`` fixture
THRESHOLD = settings.LOGIN_CAPTCHA_AFTER_FAILURES


def sign_in(client, email, password, ip="203.0.113.20", token=None):
    data = {"username": email, "password": password}
    if token:
        data[RESPONSE_FIELD] = token
    return client.post(LOGIN, data, REMOTE_ADDR=ip)


def fail(client, email, times, ip="203.0.113.20"):
    for _ in range(times):
        sign_in(client, email, "wrong-password", ip=ip, token=FAKE_PASSING_TOKEN)


def shows_widget(response) -> bool:
    return 'class="cf-turnstile"' in response.content.decode()


def test_normal_sign_in_never_sees_a_captcha(client, user):
    assert not shows_widget(client.get(LOGIN))
    fail(client, user.email, THRESHOLD - 1)
    assert sign_in(client, user.email, PASSWORD).status_code == 302


def test_after_repeated_failures_the_password_is_not_checked_without_the_captcha(client, user):
    fail(client, user.email, THRESHOLD, ip="198.51.100.1")  # another address: the email's own count applies
    failures = sum(a.failures_since_start for a in AccessAttempt.objects.all())

    response = sign_in(client, user.email, PASSWORD)  # correct password, no token
    assert response.status_code == 200 and "_auth_user_id" not in client.session
    assert shows_widget(response) and captcha.SCRIPT_URL in response.content.decode()
    assert sum(a.failures_since_start for a in AccessAttempt.objects.all()) == failures  # never reached authenticate

    assert sign_in(client, user.email, PASSWORD, token="forged").status_code == 200
    assert sign_in(client, user.email, PASSWORD, token=FAKE_PASSING_TOKEN).status_code == 302


def test_success_clears_the_email_count(client, user):
    fail(client, user.email, THRESHOLD, ip="198.51.100.1")
    sign_in(client, user.email, PASSWORD, token=FAKE_PASSING_TOKEN)
    request = RequestFactory().get(LOGIN, REMOTE_ADDR="192.0.2.50")
    assert not captcha.is_required(request, user.email)


def test_failures_from_one_address_across_emails_trigger_it(client, user):
    for index in range(THRESHOLD):
        fail(client, f"guess{index}@example.com", 1, ip="203.0.113.99")
    assert shows_widget(client.get(LOGIN, REMOTE_ADDR="203.0.113.99"))
    assert sign_in(client, user.email, PASSWORD, ip="203.0.113.99").status_code == 200  # needs the CAPTCHA
    assert not shows_widget(client.get(LOGIN, REMOTE_ADDR="192.0.2.7"))  # other addresses are unaffected
    assert sign_in(client, user.email, PASSWORD, ip="192.0.2.7").status_code == 302


@pytest.mark.parametrize("language", ["id", "en"])
def test_widget_follows_the_interface_language(client, user, language):
    fail(client, user.email, THRESHOLD)
    client.cookies[settings.LANGUAGE_COOKIE_NAME] = language
    assert f'data-language="{language}"' in client.get(LOGIN, REMOTE_ADDR="203.0.113.20").content.decode()


# --- Talking to Cloudflare -------------------------------------------------------------


def turnstile_request(token="token-from-widget"):
    return RequestFactory().post(LOGIN, {RESPONSE_FIELD: token}, REMOTE_ADDR="203.0.113.5")


@override_settings(CAPTCHA_BACKEND="turnstile", TURNSTILE_SECRET_KEY="secret-key")
@pytest.mark.parametrize(("reply", "accepted"), [
    ({"success": True, "action": "login"}, True),
    ({"success": True}, True),
    ({"success": False, "error-codes": ["invalid-input-response"]}, False),
    ({"success": True, "action": "signup"}, False),
    (["not", "an", "object"], False),
])
def test_turnstile_verification(reply, accepted):
    with mock.patch("apps.accounts.captcha.requests.post") as post:
        post.return_value.json.return_value = reply
        assert captcha.verify(turnstile_request()) is accepted
    post.assert_called_once()
    assert post.call_args.kwargs["data"] == {"secret": "secret-key", "response": "token-from-widget", "remoteip": "203.0.113.5"}


@override_settings(CAPTCHA_BACKEND="turnstile")
def test_turnstile_fails_closed():
    with mock.patch("apps.accounts.captcha.requests.post", side_effect=requests.ConnectionError):
        assert captcha.verify(turnstile_request()) is False
    with mock.patch("apps.accounts.captcha.requests.post") as post:
        assert captcha.verify(turnstile_request("")) is False
        assert captcha.verify(turnstile_request("x" * 2049)) is False
    post.assert_not_called()


# --- Other ways in and configuration ----------------------------------------------------


def test_admin_sends_everyone_to_the_protected_sign_in(client, user, django_user_model):
    response = client.get(reverse("admin:index"), follow=True)
    assert response.redirect_chain[-1][0].startswith(f"{LOGIN}?next=")

    client.force_login(user)  # not staff
    assert client.get(reverse("admin:login")).status_code == 403

    staff = django_user_model.objects.create_user(email="staff@example.com", password=PASSWORD, full_name="Staf", is_staff=True)
    client.force_login(staff)  # password-only session
    response = client.get(reverse("admin:login"), {"next": "/admin/?x=1&y=2"})
    assert response["Location"] == reverse("accounts:two_factor_verify_session") + "?next=%2Fadmin%2F%3Fx%3D1%26y%3D2"


def test_deploy_check_rejects_cloudflare_test_keys():
    with override_settings(CAPTCHA_BACKEND="turnstile", TURNSTILE_SITE_KEY="1x00000000000000000000AA"):
        assert [w.id for w in check_captcha_keys(None)] == ["core.W002"]
    with override_settings(CAPTCHA_BACKEND="turnstile", TURNSTILE_SITE_KEY="0x4AAAAAAAreal", TURNSTILE_SECRET_KEY="0x4AAAAAAAsecret"):
        assert check_captcha_keys(None) == []
