from datetime import date
from decimal import Decimal
from unittest import mock

import pytest
from django.contrib.admin.sites import site
from django.core.management import CommandError, call_command
from django.test import RequestFactory

from apps.ledger.models import JournalEntry
from apps.ledger.services import JournalLineInput, create_journal_entry
from apps.tenants.models import Organization

COMMAND = "apps.tenants.management.commands.delete_organization"


@pytest.fixture
def books(organization, account):
    create_journal_entry(
        organization=organization, date=date(2026, 1, 2), description="Setoran modal awal",
        lines=[JournalLineInput(account("1-1100"), debit=Decimal("1000")), JournalLineInput(account("3-1000"), credit=Decimal("1000"))],
    )
    return organization


def test_deletes_with_yes(books, capsys):
    call_command("delete_organization", str(books.pk), "--yes", "--skip-export")
    assert not Organization.objects.filter(pk=books.pk).exists()
    assert not JournalEntry.objects.filter(organization_id=books.pk).exists()
    assert "1 journal entr" in capsys.readouterr().out


def test_asks_for_the_organization_name(books):
    with mock.patch("builtins.input", return_value="wrong"), pytest.raises(CommandError, match="nothing was deleted"):
        call_command("delete_organization", str(books.pk), "--skip-export")
    assert Organization.objects.filter(pk=books.pk).exists()

    with mock.patch("builtins.input", return_value=f"  {books.name} "):
        call_command("delete_organization", str(books.pk), "--skip-export")
    assert not Organization.objects.filter(pk=books.pk).exists()


def test_refuses_without_owner_privileges(books):
    with mock.patch(f"{COMMAND}.can_purge_ledger", return_value=False), pytest.raises(CommandError, match="DATABASE_OWNER_URL"):
        call_command("delete_organization", str(books.pk), "--yes", "--skip-export")
    assert JournalEntry.objects.filter(organization=books).exists()


def test_unknown_organization(db):
    with pytest.raises(CommandError, match="does not exist"):
        call_command("delete_organization", "999999", "--yes", "--skip-export")


def test_admin_cannot_delete_organizations(organization, user):
    request = RequestFactory().get("/")
    request.user = user
    model_admin = site._registry[Organization]
    assert model_admin.has_delete_permission(request, organization) is False
    assert f"manage.py delete_organization {organization.pk}" in str(model_admin.deletion(organization))
