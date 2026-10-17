"""The upload pages: check, then apply; never twice; only for the organization's writers."""

import io
import re

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from openpyxl import load_workbook

from apps.audit.domain import AuditAction
from apps.audit.models import AuditEvent
from apps.contacts.models import Contact
from apps.tenants.roles import Role
from apps.uploads.importers import IMPORTERS
from apps.uploads.models import Upload


@pytest.fixture(autouse=True)
def english_user(user):
    user.language = "en"
    user.save(update_fields=["language"])


def csv_file(text, name="kontak.csv"):
    return SimpleUploadedFile(name, text.encode(), content_type="text/csv")


def token_of(response) -> str:
    match = re.search(r'name="token" value="([^"]+)"', response.content.decode())
    return match[1] if match else ""


def test_a_file_is_checked_then_applied_once(auth_client, organization):
    url = reverse("uploads:upload", args=["contacts"])
    checked = auth_client.post(url, {"file": csv_file("name,email\nPT Satu,satu@example.com\nPT Dua,\n")})
    assert checked.status_code == 200 and "Check of kontak.csv" in checked.content.decode()
    assert not Contact.objects.filter(organization=organization).exists()  # the check keeps nothing
    token = token_of(checked)
    apply = reverse("uploads:apply", args=["contacts"])
    response = auth_client.post(apply, {"token": token})
    assert response.status_code == 302 and response.url == reverse("uploads:index")
    assert Contact.objects.filter(organization=organization).count() == 2
    assert AuditEvent.objects.filter(organization=organization, action=AuditAction.FILE_UPLOADED).count() == 1
    assert auth_client.post(apply, {"token": token}).url == url  # the token is spent
    again = auth_client.post(url, {"file": csv_file("name,email\nPT Satu,satu@example.com\nPT Dua,\n")})
    assert "already applied" in again.content.decode() and not token_of(again)
    index = auth_client.get(reverse("uploads:index")).content.decode()
    assert "kontak.csv" in index and Upload.objects.get().applied == 2


def test_a_file_with_errors_offers_nothing_to_apply(auth_client, organization):
    response = auth_client.post(reverse("uploads:upload", args=["contacts"]), {"file": csv_file("name,email\nPT Satu,bukan-email\n")})
    assert not token_of(response) and "Nothing was recorded" in response.content.decode()


def test_a_token_is_bound_to_the_user_who_checked_the_file(auth_client, client, organization, make_member):
    checked = auth_client.post(reverse("uploads:upload", args=["contacts"]), {"file": csv_file("name\nPT Satu\n")})
    member = make_member(Role.ACCOUNTANT)
    client.force_login(member)
    response = client.post(reverse("uploads:apply", args=["contacts"]), {"token": token_of(checked)})
    assert response.status_code == 302 and not Contact.objects.filter(organization=organization).exists()


def test_options_are_checked_before_the_file(auth_client, organization):
    response = auth_client.post(reverse("uploads:upload", args=["opening-balances"]), {"file": csv_file("account code,debit\n1-1100,1\n")})
    assert response.status_code == 200 and not token_of(response) and response.context["options_form"].errors


def test_files_that_are_not_csv_or_xlsx_are_refused(auth_client):
    response = auth_client.post(reverse("uploads:upload", args=["contacts"]), {"file": csv_file("name\nx\n", name="kontak.exe")})
    assert response.context["form"].errors["file"]


def test_viewers_and_strangers_upload_nothing(client, organization, make_member):
    client.force_login(make_member(Role.VIEWER))
    assert client.get(reverse("uploads:index")).status_code == 200
    assert client.get(reverse("uploads:upload", args=["contacts"])).status_code == 403
    assert client.post(reverse("uploads:apply", args=["contacts"]), {"token": "x"}).status_code == 403
    assert client.get(reverse("uploads:upload", args=["unknown"])).status_code == 404


@pytest.mark.parametrize("kind", sorted(IMPORTERS))
def test_every_template_names_its_columns(auth_client, kind):
    slug = kind.replace("_", "-")
    response = auth_client.get(reverse("uploads:template", args=[slug, "csv"]))
    assert response["Content-Type"].startswith("text/csv")
    header = response.content.decode("utf-8-sig").splitlines()[0]
    assert header.split(";") == [column.label for column in IMPORTERS[kind].columns]
    workbook = auth_client.get(reverse("uploads:template", args=[slug, "xlsx"]))
    sheet = load_workbook(io.BytesIO(workbook.content)).active
    assert [cell.value for cell in sheet[1]] == [column.label for column in IMPORTERS[kind].columns]
    assert auth_client.get(reverse("uploads:upload", args=[slug])).status_code == 200


def test_checks_are_limited_per_hour(auth_client, settings):
    settings.FILE_UPLOADS_PER_HOUR = 2
    url = reverse("uploads:upload", args=["contacts"])
    for _ in range(2):
        assert token_of(auth_client.post(url, {"file": csv_file("name\nPT Satu\n")}))
    response = auth_client.post(url, {"file": csv_file("name\nPT Satu\n")})
    assert not token_of(response) and "Too many files" in response.content.decode()
