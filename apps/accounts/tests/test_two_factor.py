"""Two-factor authentication: setup, sign-in, backup codes, disabling, admin."""

import time
from contextlib import contextmanager
from datetime import timedelta
from unittest import mock

import pytest
from django.utils import timezone
from django.conf import settings
from django.core import mail
from django.urls import reverse
from django_otp import DEVICE_ID_SESSION_KEY
from django_otp.oath import TOTP

from apps.accounts import two_factor
from apps.accounts.models import AuthenticatorDevice

LOGIN = reverse("accounts:login")
OTP = reverse("accounts:login_otp")
PASSWORD = "Rahasia-123"  # from the ``user`` fixture


@contextmanager
def later(seconds=60):
    """Let django-otp's back-off period pass without sleeping."""
    target = timezone.now() + timedelta(seconds=seconds)
    with mock.patch("django_otp.models.timezone.now", return_value=target):
        yield


def code_for(device, steps_ahead=0) -> str:
    totp = TOTP(device.bin_key, device.step, device.t0, device.digits, device.drift)
    totp.time = time.time() + steps_ahead * device.step
    return f"{totp.token():0{device.digits}d}"


@pytest.fixture
def enabled(user):
    """User with an authenticator and backup codes (the setup code is already used)."""
    device = two_factor.start_setup(user)
    codes = two_factor.confirm_setup(user, device, code_for(device))
    return {"device": AuthenticatorDevice.objects.get(pk=device.pk), "backup_codes": codes}


def password_step(client, user, next_url=None):
    url = LOGIN + (f"?next={next_url}" if next_url else "")
    return client.post(url, {"username": user.email, "password": PASSWORD})


# --- Setup -----------------------------------------------------------------------------


def test_setup_flow(auth_client, user):
    page = auth_client.get(reverse("accounts:two_factor_setup"))
    assert page.context["qr_code"].startswith("data:image/svg+xml")
    device = AuthenticatorDevice.objects.get(user=user, confirmed=False)
    assert page.context["manual_key"].replace(" ", "")  # typed-in fallback

    bad_password = auth_client.post(reverse("accounts:two_factor_setup"), {"password": "nope", "code": code_for(device)})
    assert "password" in bad_password.context["form"].errors
    bad_code = auth_client.post(reverse("accounts:two_factor_setup"), {"password": PASSWORD, "code": "000000"})
    assert "code" in bad_code.context["form"].errors
    assert not two_factor.is_enabled(user)

    retyped_at_once = auth_client.post(reverse("accounts:two_factor_setup"), {"password": PASSWORD, "code": code_for(device)})
    assert "Tunggu" in str(retyped_at_once.context["form"].errors["code"])  # back-off: told to wait, not "invalid"
    with later():
        done = auth_client.post(reverse("accounts:two_factor_setup"), {"password": PASSWORD, "code": code_for(device)}, follow=True)
    assert two_factor.is_enabled(user)
    codes = done.context["new_codes"]
    assert len(codes) == settings.TWO_FACTOR_BACKUP_CODES and len(set(codes)) == len(codes)
    assert auth_client.get(reverse("accounts:two_factor_backup_codes")).context["new_codes"] is None  # shown once
    assert mail.outbox[-1].to == [user.email] and "dihidupkan" in mail.outbox[-1].body


def test_setup_key_is_stable_while_scanning(auth_client, user):
    first = auth_client.get(reverse("accounts:two_factor_setup")).context["manual_key"]
    assert auth_client.get(reverse("accounts:two_factor_setup")).context["manual_key"] == first


# --- Sign-in ---------------------------------------------------------------------------


def test_password_alone_is_not_enough(client, user, enabled):
    response = password_step(client, user)
    assert response["Location"] == OTP
    assert "_auth_user_id" not in client.session
    assert client.get(reverse("dashboard:home"))["Location"].startswith(LOGIN)


def test_sign_in_with_authenticator_code(client, user, enabled):
    password_step(client, user, next_url="/sales/")
    wrong = client.post(OTP, {"code": "000000"})
    assert wrong.status_code == 200 and wrong.context["form"].errors["code"]
    with later():
        response = client.post(OTP, {"code": code_for(enabled["device"], steps_ahead=1)})
    assert response["Location"] == "/sales/"
    assert client.session["_auth_user_id"] == str(user.pk)
    assert client.session[DEVICE_ID_SESSION_KEY] == enabled["device"].persistent_id


def test_unsafe_next_url_is_ignored(client, user, enabled):
    password_step(client, user, next_url="https://evil.example/")
    response = client.post(OTP, {"code": code_for(enabled["device"], steps_ahead=1)})
    assert response["Location"] == "/"


def test_codes_cannot_be_replayed(client, user, enabled):
    code = code_for(enabled["device"], steps_ahead=1)
    password_step(client, user)
    assert client.post(OTP, {"code": code}).status_code == 302
    client.logout()
    password_step(client, user)
    assert client.post(OTP, {"code": code}).status_code == 200  # same code again is refused


def test_backup_code_works_once(client, user, enabled):
    backup = enabled["backup_codes"][0]
    password_step(client, user)
    response = client.post(OTP, {"code": backup.upper()[:4] + " " + backup.upper()[4:]}, follow=True)  # as typed from paper
    assert response.status_code == 200 and "9" in str(list(response.context["messages"])[0])
    client.logout()
    password_step(client, user)
    assert client.post(OTP, {"code": backup}).status_code == 200


def test_too_many_wrong_codes_require_the_password_again(client, user, enabled):
    password_step(client, user)
    for attempt in range(settings.TWO_FACTOR_MAX_ATTEMPTS - 1):
        with later(seconds=60 * 2**attempt):  # each wrong code doubles the wait
            assert client.post(OTP, {"code": "000000"}).status_code == 200
    with later(seconds=60 * 2**settings.TWO_FACTOR_MAX_ATTEMPTS):
        assert client.post(OTP, {"code": "000000"})["Location"] == LOGIN
    assert client.post(OTP, {"code": code_for(enabled["device"], steps_ahead=1)})["Location"] == LOGIN


def test_pending_sign_in_expires(client, user, enabled, settings):
    password_step(client, user)
    settings.TWO_FACTOR_PENDING_SECONDS = -1
    assert client.post(OTP, {"code": code_for(enabled["device"], steps_ahead=1)})["Location"] == LOGIN


def test_code_page_needs_a_password_first(client, db):
    assert client.get(OTP)["Location"] == LOGIN


def test_wrong_passwords_are_still_rate_limited(client, user, enabled):
    for _ in range(settings.AXES_FAILURE_LIMIT - 1):
        client.post(LOGIN, {"username": user.email, "password": "wrong"})
    assert client.post(LOGIN, {"username": user.email, "password": "wrong"}).status_code == 429


def test_users_without_two_factor_sign_in_directly(client, user):
    assert password_step(client, user)["Location"] == "/"


# --- Managing ----------------------------------------------------------------------------


def test_regenerating_backup_codes_needs_password_and_invalidates_old_ones(client, user, enabled):
    client.force_login(user)
    url = reverse("accounts:two_factor_backup_codes")
    assert "password" in client.post(url, {"password": "nope"}).context["form"].errors
    new_codes = client.post(url, {"password": PASSWORD}, follow=True).context["new_codes"]
    assert set(new_codes).isdisjoint(enabled["backup_codes"])
    assert two_factor.verify(user, enabled["backup_codes"][1]) is None
    with later():
        assert two_factor.verify(user, new_codes[0]) is not None


def test_turning_off_needs_password_and_code(client, user, enabled):
    client.force_login(user)
    url = reverse("accounts:two_factor_disable")
    assert "code" in client.post(url, {"password": PASSWORD, "code": "000000"}).context["form"].errors
    assert two_factor.is_enabled(user)
    with later():
        client.post(url, {"password": PASSWORD, "code": code_for(enabled["device"], steps_ahead=1)})
    assert not two_factor.is_enabled(user)
    assert "dimatikan" in mail.outbox[-1].body
    client.logout()
    assert password_step(client, user)["Location"] == "/"


# --- Admin -------------------------------------------------------------------------------


def test_admin_requires_a_verified_second_factor(client, django_user_model):
    admin = django_user_model.objects.create_superuser(email="root@example.com", password=PASSWORD, full_name="Root")
    client.force_login(admin)
    assert client.get(reverse("admin:index")).status_code == 302  # password-only session: refused

    device = two_factor.start_setup(admin)
    two_factor.confirm_setup(admin, device, code_for(device))
    client.logout()
    client.post(LOGIN, {"username": admin.email, "password": PASSWORD})
    client.post(OTP, {"code": code_for(AuthenticatorDevice.objects.get(pk=device.pk), steps_ahead=1)})
    assert client.get(reverse("admin:index")).status_code == 200
