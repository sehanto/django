"""Light or dark pages: the device's setting, or the user's choice (saved on the account, or in a cookie)."""

from django.test import Client
from django.urls import reverse

from apps.accounts.models import Theme


def test_pages_follow_the_device_until_the_user_chooses(auth_client, user):
    page = auth_client.get(reverse("dashboard:home")).content.decode()
    assert 'data-theme="system"' in page and 'content="light dark"' in page
    response = auth_client.post(reverse("accounts:set_theme"), {"theme": Theme.DARK, "next": reverse("contacts:list")})
    assert response.status_code == 302 and response.url == reverse("contacts:list")
    user.refresh_from_db()
    assert user.theme == Theme.DARK
    page = auth_client.get(reverse("contacts:list")).content.decode()
    assert 'data-theme="dark"' in page and '<meta name="color-scheme" content="dark">' in page
    assert 'aria-pressed="true"' in page


def test_signed_out_visitors_keep_their_choice_in_a_cookie(client, db):
    client.post(reverse("accounts:set_theme"), {"theme": Theme.LIGHT})
    assert client.cookies["theme"].value == Theme.LIGHT
    assert 'data-theme="light"' in client.get(reverse("accounts:login")).content.decode()


def test_unknown_values_change_nothing(auth_client, user):
    response = auth_client.post(reverse("accounts:set_theme"), {"theme": "neon", "next": "https://evil.example/"})
    assert response.url == "/"
    user.refresh_from_db()
    assert user.theme == Theme.SYSTEM
    client = Client()
    client.cookies["theme"] = "<script>"
    assert 'data-theme="system"' in client.get(reverse("accounts:login")).content.decode()


def test_the_profile_saves_the_appearance(auth_client, user):
    auth_client.post(reverse("accounts:profile"), {"full_name": user.full_name, "phone": "", "language": "id", "theme": Theme.LIGHT})
    user.refresh_from_db()
    assert user.theme == Theme.LIGHT
