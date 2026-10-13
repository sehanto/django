"""Login rate limiting with django-axes: lock a username+IP pair after repeated failures."""

from datetime import timedelta

import pytest
from axes.models import AccessAttempt
from django.conf import settings
from django.core.management import call_command
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone

from apps.accounts.captcha import FAKE_PASSING_TOKEN, RESPONSE_FIELD
from apps.accounts.client_ip import client_ip, login_username

LOGIN = reverse("accounts:login")
PASSWORD = "Rahasia-123"  # from the ``user`` fixture
LIMIT = settings.AXES_FAILURE_LIMIT


def attempt(client, email, password="wrong-password", ip="203.0.113.10", path=LOGIN):
    """A sign-in by someone who also solves the CAPTCHA that appears after a few failures:
    the lockout must still stop them."""
    return client.post(path, {"username": email, "password": password, RESPONSE_FIELD: FAKE_PASSING_TOKEN}, REMOTE_ADDR=ip)


def fail(client, email, times, **kwargs):
    for _ in range(times):
        assert attempt(client, email, **kwargs).status_code == 200  # normal "wrong password" form


def lock(client, email, **kwargs):
    """Fail up to the limit; the failure that reaches it already returns the lockout page."""
    fail(client, email, LIMIT - 1, **kwargs)
    assert attempt(client, email, **kwargs).status_code == 429


def test_lockout_after_limit_blocks_even_the_correct_password(client, user):
    fail(client, user.email, LIMIT - 1)
    locked = attempt(client, user.email)
    assert locked.status_code == 429
    assert "Terlalu banyak percobaan masuk" in locked.content.decode()
    assert "15\xa0menit" in locked.content.decode()

    blocked = attempt(client, user.email, password=PASSWORD)
    assert blocked.status_code == 429
    assert "_auth_user_id" not in client.session


def test_lock_is_scoped_to_username_and_ip(client, user, django_user_model):
    other = django_user_model.objects.create_user(email="other@example.com", password=PASSWORD, full_name="Lain")
    lock(client, user.email)

    # Same account from another network still works (an attacker can't lock users out globally)...
    assert attempt(client, user.email, password=PASSWORD, ip="198.51.100.20").status_code == 302
    client.logout()
    # ...and other accounts behind the same (shared, carrier-NAT) IP are unaffected.
    assert attempt(client, other.email, password=PASSWORD).status_code == 302


def test_successful_login_resets_the_counter(client, user):
    fail(client, user.email, LIMIT - 1)
    assert attempt(client, user.email, password=PASSWORD).status_code == 302
    client.logout()
    fail(client, user.email, LIMIT - 1)  # would be locked if earlier failures still counted
    assert attempt(client, user.email, password=PASSWORD).status_code == 302


def test_lock_expires_after_cool_off(client, user):
    lock(client, user.email)
    assert attempt(client, user.email, password=PASSWORD).status_code == 429
    AccessAttempt.objects.update(attempt_time=timezone.now() - settings.AXES_COOLOFF_TIME - timedelta(seconds=1))
    assert attempt(client, user.email, password=PASSWORD).status_code == 302


def test_email_is_case_insensitive_for_login_and_counting(client, user):
    for variant in ["OWNER@example.com", "Owner@Example.com", "owner@EXAMPLE.COM", "owner@example.com"][: LIMIT - 1]:
        assert attempt(client, variant).status_code == 200
    assert attempt(client, "OwNeR@example.com").status_code == 429  # all variants share one counter
    AccessAttempt.objects.all().delete()
    assert attempt(client, "OWNER@Example.com", password=PASSWORD).status_code == 302


def test_lockout_page_follows_language(client, user):
    client.post(reverse("accounts:set_language"), {"language": "en", "next": "/"})
    fail(client, user.email, LIMIT - 1)
    html = attempt(client, user.email).content.decode()
    assert "Too many sign-in attempts" in html and "15\xa0minutes" in html  # Django keeps "15 minutes" together


def test_admin_has_no_login_form_of_its_own(client, django_user_model):
    """Its own form would bypass the CAPTCHA; everyone signs in on the protected page."""
    django_user_model.objects.create_superuser(email="admin@example.com", password=PASSWORD, full_name="Admin")
    response = attempt(client, "admin@example.com", path=reverse("admin:login"))
    assert response.status_code == 302 and response["Location"].startswith(LOGIN)
    assert "_auth_user_id" not in client.session


def test_support_can_unlock_a_user(client, user):
    lock(client, user.email)
    call_command("axes_reset_username", user.email, verbosity=0)
    assert attempt(client, user.email, password=PASSWORD).status_code == 302


def test_login_username_normalization():
    request = RequestFactory().post(LOGIN)
    assert login_username(request, {"username": "  Rina@Example.COM "}) == "rina@example.com"


@pytest.mark.parametrize(
    ("proxies", "forwarded", "remote", "expected"),
    [
        (0, "1.2.3.4", "203.0.113.7", "203.0.113.7"),  # no proxy: header ignored entirely
        (1, "203.0.113.7", "10.0.0.2", "203.0.113.7"),  # nginx appended the client
        (1, "1.2.3.4, 203.0.113.7", "10.0.0.2", "203.0.113.7"),  # client-forged entry on the left is ignored
        (2, "1.2.3.4, 203.0.113.7, 172.16.0.9", "10.0.0.2", "203.0.113.7"),  # CDN + nginx
        (2, "203.0.113.7", "10.0.0.2", "10.0.0.2"),  # fewer entries than proxies: don't trust it
        (1, "not-an-ip", "10.0.0.2", "10.0.0.2"),
        (1, "2001:db8::1", "10.0.0.2", "2001:db8::1"),
    ],
)
def test_client_ip_behind_trusted_proxies(settings, proxies, forwarded, remote, expected):
    settings.TRUSTED_PROXY_COUNT = proxies
    request = RequestFactory().get("/", HTTP_X_FORWARDED_FOR=forwarded, REMOTE_ADDR=remote)
    assert client_ip(request) == expected
