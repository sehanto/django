"""Login CAPTCHA (Cloudflare Turnstile): invisible to normal users, required after repeated failures."""

from unittest import mock

import pytest
import requests
from axes.models import AccessAttempt
from django.conf import settings
from django.core import mail
from django.test import RequestFactory, override_settings
from django.urls import reverse

from apps.accounts import captcha, two_factor
from apps.accounts.captcha import FAKE_PASSING_TOKEN, RESPONSE_FIELD
from apps.accounts.models import User
from apps.accounts.tests.test_two_factor import code_for
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
    return 'class="cf-turnstile' in response.content.decode()


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


# --- Sign-up: always required ----------------------------------------------------------

SIGNUP = reverse("accounts:signup")


def sign_up(client, email="dewi@example.com", token=None):
    data = {"full_name": "Dewi Lestari", "email": email, "password": "Kuat-Sekali-99",
            "organization_name": "Batik Dewi", "legal_form": "perorangan"}
    if token:
        data[RESPONSE_FIELD] = token
    return client.post(SIGNUP, data)


def test_sign_up_always_shows_the_captcha(client, db):
    html = client.get(SIGNUP).content.decode()
    assert 'class="cf-turnstile' in html and 'data-action="signup"' in html and captcha.SCRIPT_URL in html


def test_sign_up_needs_a_solved_captcha(client, db):
    for token in (None, "forged"):
        response = sign_up(client, token=token)
        assert response.status_code == 200 and not User.objects.filter(email="dewi@example.com").exists()
    assert sign_up(client, token=FAKE_PASSING_TOKEN).status_code == 302
    assert User.objects.filter(email="dewi@example.com").exists()


def test_sign_up_reveals_nothing_about_existing_accounts_without_the_captcha(client, user):
    response = sign_up(client, email=user.email)
    errors = response.context["form"].errors
    assert list(errors) == ["__all__"]  # only the security check, no "already registered"
    assert "already registered" not in str(errors) and "sudah terdaftar" not in str(errors)
    assert "email" in sign_up(client, email=user.email, token=FAKE_PASSING_TOKEN).context["form"].errors


# --- Password reset: always required ---------------------------------------------------

RESET = reverse("accounts:password_reset")


def test_password_reset_needs_a_solved_captcha_before_anything_happens(client, user):
    html = client.get(RESET).content.decode()
    assert 'data-action="password_reset"' in html and captcha.SCRIPT_URL in html

    for _ in range(settings.PASSWORD_RESET_EMAILS_PER_ADDRESS_PER_HOUR + 1):  # would exhaust the limit if counted
        response = client.post(RESET, {"email": user.email})
        assert response.status_code == 200 and list(response.context["form"].errors) == ["__all__"]
    assert mail.outbox == []

    response = client.post(RESET, {"email": user.email, RESPONSE_FIELD: FAKE_PASSING_TOKEN})
    assert response.status_code == 302 and len(mail.outbox) == 1  # the limit was not used up


# --- Resending the confirmation link: always required ----------------------------------


def test_resending_the_confirmation_link_needs_a_solved_captcha(client, db):
    sign_up(client, token=FAKE_PASSING_TOKEN)  # signed in, email not confirmed yet; one email sent
    page = client.get(reverse("accounts:verify_email_pending")).content.decode()
    assert 'data-action="resend_verification"' in page and captcha.SCRIPT_URL in page

    resend = reverse("accounts:verify_email_resend")
    for _ in range(settings.EMAIL_VERIFICATION_EMAILS_PER_HOUR + 1):  # would exhaust the limit if counted
        client.post(resend)
    assert len(mail.outbox) == 1

    client.post(resend, {RESPONSE_FIELD: FAKE_PASSING_TOKEN})
    assert len(mail.outbox) == 2  # the hourly limit was not used up


def test_correcting_the_address_needs_a_solved_captcha_before_the_password_is_checked(client, db):
    sign_up(client, email="dewi@exmaple.com", token=FAKE_PASSING_TOKEN)
    page = client.get(reverse("accounts:verify_email_pending")).content.decode()
    assert 'data-action="change_email"' in page and 'data-action="resend_verification"' in page

    change = reverse("accounts:verify_email_change")
    wrong = client.post(change, {"email": "dewi@example.com", "password": "nope"})
    assert list(wrong.context["change_form"].errors) == ["__all__"]  # no "password is incorrect" without it
    assert User.objects.filter(email="dewi@exmaple.com").exists() and len(mail.outbox) == 1

    client.post(change, {"email": "dewi@example.com", "password": "Kuat-Sekali-99", RESPONSE_FIELD: FAKE_PASSING_TOKEN})
    assert User.objects.filter(email="dewi@example.com").exists()


# --- Changing the password: always required --------------------------------------------


def test_changing_the_password_needs_a_solved_captcha_before_anything_is_checked(auth_client, user):
    change = reverse("accounts:password_change")
    assert 'data-action="change_password"' in auth_client.get(change).content.decode()
    new = {"new_password1": "Baru-Rahasia-456", "new_password2": "Baru-Rahasia-456"}

    wrong = auth_client.post(change, {"old_password": "a-guess", **new})
    assert list(wrong.context["form"].errors) == ["__all__"]  # no "old password incorrect" without it
    right_without = auth_client.post(change, {"old_password": PASSWORD, **new})
    assert right_without.status_code == 200
    user.refresh_from_db()
    assert user.check_password(PASSWORD)

    assert auth_client.post(change, {"old_password": PASSWORD, **new, RESPONSE_FIELD: FAKE_PASSING_TOKEN}).status_code == 302
    user.refresh_from_db()
    assert user.check_password("Baru-Rahasia-456")


# --- Two-factor settings: always required -----------------------------------------------


@pytest.fixture
def two_factor_on(user):
    device = two_factor.start_setup(user)
    two_factor.confirm_setup(user, device, code_for(device))
    return two_factor


def test_turning_on_two_factor_needs_a_solved_captcha_before_the_code_is_tried(auth_client, user):
    url = reverse("accounts:two_factor_setup")
    assert 'data-action="enable_two_factor"' in auth_client.get(url).content.decode()
    device = two_factor.start_setup(user)
    with mock.patch.object(two_factor, "confirm_setup", wraps=two_factor.confirm_setup) as confirm:
        response = auth_client.post(url, {"password": PASSWORD, "code": code_for(device)})
    assert list(response.context["form"].errors) == ["__all__"]
    confirm.assert_not_called()
    assert not two_factor.is_enabled(user)

    auth_client.post(url, {"password": PASSWORD, "code": code_for(device), RESPONSE_FIELD: FAKE_PASSING_TOKEN})
    assert two_factor.is_enabled(user)


def test_proving_two_factor_for_a_session_needs_a_solved_captcha(auth_client, user, two_factor_on):
    url = reverse("accounts:two_factor_verify_session")
    assert 'data-action="verify_session"' in auth_client.get(url).content.decode()
    with mock.patch.object(two_factor, "verify", wraps=two_factor.verify) as verify:
        response = auth_client.post(url, {"code": "123456"})
    assert list(response.context["form"].errors) == ["__all__"]
    verify.assert_not_called()  # no code attempt counted against the user
    assert not auth_client.session.get("otp_device_id")


def test_creating_backup_codes_needs_a_solved_captcha(auth_client, user, two_factor_on):
    url = reverse("accounts:two_factor_backup_codes")
    assert 'data-action="backup_codes"' in auth_client.get(url).content.decode()
    before = two_factor_on.remaining_backup_codes(user)

    response = auth_client.post(url, {"password": "a-guess"})
    assert list(response.context["form"].errors) == ["__all__"]  # no "password is incorrect" without it
    auth_client.post(url, {"password": PASSWORD})
    assert auth_client.get(url).context["new_codes"] is None and mail.outbox == []

    auth_client.post(url, {"password": PASSWORD, RESPONSE_FIELD: FAKE_PASSING_TOKEN})
    assert two_factor_on.remaining_backup_codes(user) == before  # a fresh full set
    assert len(mail.outbox) == 1  # the security notice


def test_turning_off_two_factor_needs_a_solved_captcha_before_the_code_is_tried(auth_client, user, two_factor_on):
    url = reverse("accounts:two_factor_disable")
    assert 'data-action="disable_two_factor"' in auth_client.get(url).content.decode()
    with mock.patch.object(two_factor_on, "verify", wraps=two_factor_on.verify) as verify:
        response = auth_client.post(url, {"password": PASSWORD, "code": "123456"})
    assert list(response.context["form"].errors) == ["__all__"]
    verify.assert_not_called()  # no code attempt counted against the user
    assert two_factor_on.is_enabled(user)


# --- Talking to Cloudflare -------------------------------------------------------------


def turnstile_request(token="token-from-widget"):
    return RequestFactory().post(LOGIN, {RESPONSE_FIELD: token}, REMOTE_ADDR="203.0.113.5")


@override_settings(CAPTCHA_BACKEND="turnstile", TURNSTILE_SECRET_KEY="secret-key")
@pytest.mark.parametrize(("reply", "accepted"), [
    ({"success": True, "action": "login"}, True),
    ({"success": True}, True),
    ({"success": False, "error-codes": ["invalid-input-response"]}, False),
    ({"success": True, "action": "signup"}, False),  # solved for the sign-up form
    (["not", "an", "object"], False),
])
def test_turnstile_verification(reply, accepted):
    with mock.patch("apps.accounts.captcha.requests.post") as post:
        post.return_value.json.return_value = reply
        assert captcha.verify(turnstile_request(), captcha.CaptchaAction.LOGIN) is accepted
    post.assert_called_once()
    assert post.call_args.kwargs["data"] == {"secret": "secret-key", "response": "token-from-widget", "remoteip": "203.0.113.5"}


@override_settings(CAPTCHA_BACKEND="turnstile")
@pytest.mark.parametrize("action", list(captcha.CaptchaAction))
def test_each_form_only_accepts_its_own_tokens(action):
    for solved_for in captcha.CaptchaAction:
        with mock.patch("apps.accounts.captcha.requests.post") as post:
            post.return_value.json.return_value = {"success": True, "action": solved_for}
            assert captcha.verify(turnstile_request(), action) is (solved_for == action)


@override_settings(CAPTCHA_BACKEND="turnstile")
def test_turnstile_fails_closed():
    with mock.patch("apps.accounts.captcha.requests.post", side_effect=requests.ConnectionError):
        assert captcha.verify(turnstile_request(), captcha.CaptchaAction.LOGIN) is False
    with mock.patch("apps.accounts.captcha.requests.post") as post:
        assert captcha.verify(turnstile_request(""), captcha.CaptchaAction.LOGIN) is False
        assert captcha.verify(turnstile_request("x" * 2049), captcha.CaptchaAction.LOGIN) is False
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
