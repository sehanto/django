"""'Remember this device for 30 days' on the two-factor sign-in step."""

import hashlib
import time
from datetime import timedelta

import pytest
from django.conf import settings
from django.core import mail
from django.urls import reverse
from django.utils import timezone
from django_otp import DEVICE_ID_SESSION_KEY
from django_otp.oath import TOTP

from apps.accounts.captcha import FAKE_PASSING_TOKEN, RESPONSE_FIELD
from apps.accounts import two_factor
from apps.accounts.models import AuthenticatorDevice, TrustedDevice
from apps.accounts.trusted_devices import COOKIE_NAME

pytestmark = pytest.mark.usefixtures("still_clock")  # authenticator codes: see conftest

LOGIN = reverse("accounts:login")
OTP = reverse("accounts:login_otp")
PASSWORD = "Rahasia-123"
CHROME = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36"


def code_for(device, steps_ahead=1) -> str:
    totp = TOTP(device.bin_key, device.step, device.t0, device.digits, device.drift)
    totp.time = time.time() + steps_ahead * device.step
    return f"{totp.token():0{device.digits}d}"


@pytest.fixture
def device(user):
    totp = two_factor.start_setup(user)
    two_factor.confirm_setup(user, totp, code_for(totp, steps_ahead=0))
    return AuthenticatorDevice.objects.get(pk=totp.pk)


def sign_in(client, user, password=PASSWORD):
    return client.post(LOGIN, {"username": user.email, "password": password}, HTTP_USER_AGENT=CHROME)


def sign_in_with_code(client, user, device, *, remember, steps_ahead=1):
    sign_in(client, user)
    data = {"code": code_for(device, steps_ahead), RESPONSE_FIELD: FAKE_PASSING_TOKEN}
    if remember:
        data["remember"] = "on"
    return client.post(OTP, data, HTTP_USER_AGENT=CHROME)


def sign_out(client):
    # Like a browser: the session ends, other cookies stay. (client.logout() would wipe all cookies.)
    client.post(reverse("accounts:logout"))


def remembered_browser(client, user, device):
    response = sign_in_with_code(client, user, device, remember=True)
    sign_out(client)
    return response


def test_remembering_sets_a_hardened_cookie_and_stores_only_a_hash(client, user, device):
    response = sign_in_with_code(client, user, device, remember=True)
    cookie = response.cookies[COOKIE_NAME]
    assert cookie["path"] == "/account/" and cookie["httponly"] and cookie["samesite"] == "Lax"
    assert cookie["max-age"] == settings.TWO_FACTOR_REMEMBER_DAYS * 24 * 3600
    device_id, secret = cookie.value.split(":")
    stored = TrustedDevice.objects.get(pk=device_id)
    assert stored.token_hash == hashlib.sha256(secret.encode()).hexdigest() and secret not in stored.token_hash
    assert stored.label == "Chrome · Windows"
    assert "Chrome · Windows" in mail.outbox[-1].body  # account holder is told


def test_remembered_browser_skips_only_the_code(client, user, device):
    remembered_browser(client, user, device)
    assert sign_in(client, user, password="wrong").status_code == 200  # the password is still required
    response = sign_in(client, user)
    assert response["Location"] == "/"  # straight in, no code page
    assert client.session[DEVICE_ID_SESSION_KEY] == device.persistent_id  # counts as a verified session
    assert TrustedDevice.objects.get().last_used_at is not None


def test_without_ticking_the_box_the_code_is_asked_again(client, user, device):
    response = sign_in_with_code(client, user, device, remember=False)
    assert COOKIE_NAME not in response.cookies
    sign_out(client)
    assert sign_in(client, user)["Location"] == OTP


def test_cookie_is_bound_to_its_user(client, user, device, django_user_model):
    remembered_browser(client, user, device)
    other = django_user_model.objects.create_user(email="lain@example.com", password=PASSWORD, full_name="Lain", email_verified_at=timezone.now())
    other_device = two_factor.start_setup(other)
    two_factor.confirm_setup(other, other_device, code_for(other_device, steps_ahead=0))
    assert sign_in(client, other)["Location"] == OTP


def test_tampered_cookie_is_ignored(client, user, device):
    remembered_browser(client, user, device)
    device_id = client.cookies[COOKIE_NAME].value.split(":")[0]
    client.cookies[COOKIE_NAME] = f"{device_id}:guessed-secret"
    assert sign_in(client, user)["Location"] == OTP


def test_expired_remembered_device_asks_again(client, user, device):
    remembered_browser(client, user, device)
    TrustedDevice.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    assert sign_in(client, user)["Location"] == OTP


def test_password_change_forgets_every_remembered_device(client, user, device):
    remembered_browser(client, user, device)
    user.set_password("Baru-Sekali-456")
    user.save()
    assert sign_in(client, user, password="Baru-Sekali-456")["Location"] == OTP
    assert not TrustedDevice.objects.exists()


def test_turning_off_2fa_forgets_devices(client, user, device):
    remembered_browser(client, user, device)
    client.force_login(user)
    client.post(reverse("accounts:two_factor_disable"), {"password": PASSWORD, "code": code_for(device, steps_ahead=2), RESPONSE_FIELD: FAKE_PASSING_TOKEN})
    assert not TrustedDevice.objects.exists()


def test_forget_one_or_all_from_the_security_page(client, user, device):
    remembered_browser(client, user, device)
    sign_in(client, user)
    page = client.get(reverse("accounts:security"))
    assert page.context["this_device_id"] == TrustedDevice.objects.get().pk
    TrustedDevice.objects.create(user=user, token_hash="x" * 64, password_fingerprint="-", label="Safari · iOS",
                                 expires_at=timezone.now() + timedelta(days=5))
    client.post(reverse("accounts:forget_trusted_device"), {"device": page.context["this_device_id"]})
    assert list(TrustedDevice.objects.values_list("label", flat=True)) == ["Safari · iOS"]
    client.post(reverse("accounts:forget_trusted_device"))
    assert not TrustedDevice.objects.exists()


def test_staff_always_enter_a_code(client, user, device):
    user.is_staff = True
    user.save()
    sign_in(client, user)
    assert "remember" not in client.get(OTP).context["form"].fields  # not offered
    client.post(OTP, {RESPONSE_FIELD: FAKE_PASSING_TOKEN, "code": code_for(device), "remember": "on"})
    sign_out(client)
    assert not TrustedDevice.objects.exists()
    # Even a remembered device created earlier (e.g. before becoming staff) is ignored.
    user.is_staff = False
    user.save()
    remembered_browser(client, user, device)
    user.is_staff = True
    user.save()
    assert sign_in(client, user)["Location"] == OTP


def test_remembered_sign_in_satisfies_an_organization_requirement(client, user, device, organization):
    organization.require_two_factor = True
    organization.save()
    remembered_browser(client, user, device)
    sign_in(client, user)
    assert client.get(reverse("dashboard:home")).status_code == 200
