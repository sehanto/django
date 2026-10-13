"""Interface language: Indonesian by default, English on request, saved per user."""

from datetime import date

import pytest
from django.conf import settings
from django.urls import reverse
from django.utils import translation

from apps.accounts.captcha import FAKE_PASSING_TOKEN, RESPONSE_FIELD
from apps.accounts.models import User
from apps.core.exceptions import PeriodLocked
from apps.invoicing.models import Invoice
from apps.ledger.domain import ensure_period_open

SET_LANGUAGE = reverse("accounts:set_language")


@pytest.mark.parametrize(
    ("accept_language", "expected"),
    [("", "id"), ("id-ID,id;q=0.9", "id"), ("en-US,en;q=0.9", "en"), ("fr-FR,fr;q=0.9", "id")],
)
def test_first_visit_uses_browser_language_as_a_hint(client, db, accept_language, expected):
    response = client.get(reverse("accounts:login"), HTTP_ACCEPT_LANGUAGE=accept_language)
    assert f'<html lang="{expected}">' in response.content.decode()
    assert response["Content-Language"] == expected


def test_explicit_choice_beats_browser_language(client, db):
    client.post(SET_LANGUAGE, {"language": "id", "next": "/"})
    response = client.get(reverse("accounts:login"), HTTP_ACCEPT_LANGUAGE="en-US,en;q=0.9")
    assert '<html lang="id">' in response.content.decode()


def test_responses_vary_on_language_signals(client, db):
    vary = client.get(reverse("accounts:login"))["Vary"]
    assert "Accept-Language" in vary and "Cookie" in vary


def test_visitor_can_switch_to_english_with_cookie(client, db):
    response = client.post(SET_LANGUAGE, {"language": "en", "next": reverse("accounts:signup")})
    assert response.status_code == 302
    assert response["Location"] == reverse("accounts:signup")
    assert response.cookies[settings.LANGUAGE_COOKIE_NAME].value == "en"

    html = client.get(reverse("accounts:signup")).content.decode()
    assert '<html lang="en">' in html
    assert "Sign up and create your business" in html


def test_signed_in_choice_is_saved_on_account_and_wins_over_cookie(auth_client, user):
    auth_client.post(SET_LANGUAGE, {"language": "en", "next": "/"})
    user.refresh_from_db()
    assert user.language == "en"

    auth_client.cookies[settings.LANGUAGE_COOKIE_NAME] = "id"  # e.g. another browser tab
    html = auth_client.get(reverse("dashboard:home")).content.decode()
    assert "Revenue vs expenses" in html
    assert "Accounts receivable" in html


def test_unknown_language_and_foreign_redirects_are_ignored(auth_client, user):
    response = auth_client.post(SET_LANGUAGE, {"language": "fr", "next": "https://evil.example.com/"})
    assert response["Location"] == "/"
    user.refresh_from_db()
    assert user.language == "id"


def test_language_can_be_changed_in_profile(auth_client, user):
    response = auth_client.post(reverse("accounts:profile"), {"full_name": user.full_name, "phone": "", "language": "en"}, follow=True)
    user.refresh_from_db()
    assert user.language == "en"
    assert "Profile updated." in response.content.decode()


def test_signup_keeps_the_language_being_browsed(client, db):
    client.post(SET_LANGUAGE, {"language": "en", "next": "/"})
    client.post(reverse("accounts:signup"), {
        "full_name": "Made Wirawan", "email": "made@example.com", "password": "Kuat-Sekali-99",
        "organization_name": "Warung Made", "legal_form": "perorangan", RESPONSE_FIELD: FAKE_PASSING_TOKEN,
    })
    assert User.objects.get(email="made@example.com").language == "en"


def test_business_rule_messages_follow_active_language():
    with translation.override("en"), pytest.raises(PeriodLocked, match="is locked"):
        ensure_period_open(date(2026, 1, 1), date(2026, 1, 31))
    with translation.override("id"), pytest.raises(PeriodLocked, match="sudah dikunci"):
        ensure_period_open(date(2026, 1, 1), date(2026, 1, 31))


def test_model_choice_labels_are_translated():
    with translation.override("en"):
        assert str(Invoice.Status.PAID.label) == "Paid"
    with translation.override("id"):
        assert str(Invoice.Status.PAID.label) == "Lunas"


def test_language_picked_on_sign_in_page_is_kept_after_login(client, user):
    client.post(SET_LANGUAGE, {"language": "en", "next": reverse("accounts:login")})
    client.post(reverse("accounts:login"), {"username": user.email, "password": "Rahasia-123"})
    user.refresh_from_db()
    assert user.language == "en"
    assert "My profile" in client.get(reverse("accounts:profile")).content.decode()


def test_counts_use_correct_plural_forms(auth_client, user, organization):
    from apps.tenants.models import Plan

    Plan.objects.create(code="solo", name="Solo", max_users=1, max_invoices_per_month=1)
    user.language = "en"
    user.save(update_fields=["language"])
    html = auth_client.get(reverse("billing:overview")).content.decode()
    assert "1 user<" in html and "1 users" not in html
    assert "1 invoice/month" in html


def test_money_follows_reader_locale():
    from apps.core.formatting import localized_rupiah

    with translation.override("id"):
        assert localized_rupiah("1234567.5") == "Rp 1.234.567,50"
    with translation.override("en"):
        assert localized_rupiah("1234567.5") == "Rp 1,234,567.50"


def test_password_with_surrounding_spaces_works_for_sign_up_and_login(client, db):
    password = " Kuat-Sekali-99 "
    client.post(reverse("accounts:signup"), {
        "full_name": "Ayu", "email": "ayu@example.com", "password": password,
        "organization_name": "Warung Ayu", "legal_form": "perorangan", RESPONSE_FIELD: FAKE_PASSING_TOKEN,
    })
    client.logout()
    response = client.post(reverse("accounts:login"), {"username": "ayu@example.com", "password": password})
    assert response.status_code == 302
