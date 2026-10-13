import csv
import io
import zipfile
from datetime import date
from decimal import Decimal

import pytest
from django.apps import apps
from django.core.cache import cache
from django.core.management import CommandError, call_command
from django.test import override_settings
from django.urls import reverse
from django.utils import translation

from apps.contacts.models import Contact
from apps.core.models import DocumentSequence, TenantOwnedModel
from apps.invoicing import services as invoicing
from apps.invoicing.models import InvoiceKind
from apps.invoicing.services import InvoiceLineInput
from apps.ledger.models import JournalEntry, JournalLine
from apps.ledger.services import JournalLineInput, create_journal_entry
from apps.reports.exports import ENTRY_LINES, TABLES, write_books_archive
from apps.tenants.roles import Role

D = Decimal


@pytest.fixture(autouse=True)
def fresh_throttle():
    cache.clear()


@pytest.fixture
def books(organization, customer, account):
    create_journal_entry(
        organization=organization, date=date(2026, 1, 2), description="Setoran modal awal",
        lines=[JournalLineInput(account("1-1100"), debit=D("10000000")), JournalLineInput(account("3-1000"), credit=D("10000000"))],
    )
    invoice = invoicing.create_invoice(
        organization=organization, kind=InvoiceKind.SALES, contact=customer, date=date(2026, 1, 5),
        lines=[InvoiceLineInput("Kopi bubuk 1kg", account("4-1000"), D("2"), D("125000.50"))],
    )
    invoicing.record_payment(
        invoice=invoicing.issue_invoice(invoice=invoice), date=date(2026, 1, 6), amount=D("100000"), cash_account=account("1-1000"),
    )
    return organization


def read_archive(data: bytes) -> dict[str, list[list[str]]]:
    archive = zipfile.ZipFile(io.BytesIO(data))
    return {
        name: list(csv.reader(io.StringIO(archive.read(name).decode("utf-8-sig")), delimiter=";"))
        for name in archive.namelist() if name.endswith(".csv")
    } | {"README.txt": archive.read("README.txt").decode("utf-8-sig")}


def export(organization, language="en", **kwargs) -> dict:
    buffer = io.BytesIO()
    with translation.override(language):
        write_books_archive(organization, buffer, **kwargs)
    return read_archive(buffer.getvalue())


def test_every_tenant_table_is_exported():
    exported = {model for _table, model, _lookup, _ordering in TABLES} | {JournalEntry}  # entries via their lines
    not_books = {DocumentSequence}  # numbering counters
    tenant_models = {m for m in apps.get_models() if issubclass(m, TenantOwnedModel)}
    assert tenant_models - not_books <= exported, "add new tenant data to apps/reports/exports.py TABLES"


def test_archive_holds_the_complete_books(books, other_organization):
    files = export(books)
    assert set(files) == {
        "README.txt", "organization.csv", "chart_of_accounts.csv", "journal_entries.csv", "contacts.csv", "items.csv",
        "stock_movements.csv", "invoices.csv", "invoice_lines.csv", "returns.csv", "return_lines.csv", "payments.csv", "tax_rates.csv", "business_places.csv", "fixed_assets.csv", "asset_revisions.csv", "depreciation.csv", "pph_final_expenses.csv", "pph_final_payments.csv", "trial_balance.csv",
    }
    journal = files["journal_entries.csv"]
    assert journal[0][:5] == ["Number", "Date", "Status", "Source", "Entry description"]
    assert len(journal[0]) == len(ENTRY_LINES.columns)
    assert len(journal) - 1 == JournalLine.objects.filter(entry__organization=books).count() == 6
    assert ["JU/2026/01/0001", "2026-01-02", "Posted", "General journal", "Setoran modal awal"] == journal[1][:5]
    row = dict(zip(journal[0], journal[1]))
    assert (row["Debit"], row["Credit"]) == ("10000000.00", "0.00")
    assert row["Posting sequence"] == "1" and len(row["Entry hash"]) == 64

    invoice = files["invoices.csv"][1]
    assert invoice[1:4] == ["Sales", "INV/2026/01/0001", "PT Pelanggan"] and "250001.00" in invoice
    assert files["invoice_lines.csv"][1][2] == "Kopi bubuk 1kg"
    assert files["payments.csv"][1][5:7] == ["100000.00", "Bank transfer"]
    assert files["trial_balance.csv"][-1][2] == files["trial_balance.csv"][-1][3]  # debits = credits
    assert ["Business name", books.name] in files["organization.csv"]
    assert books.name in files["README.txt"] and "UU KUP" in files["README.txt"]

    other_names = {other_organization.name}
    assert not any(cell in other_names for rows in files.values() if isinstance(rows, list) for row in rows for cell in row)


def test_headers_follow_the_language_and_data_stays_as_recorded(books):
    files = export(books, language="id")
    assert files["journal_entries.csv"][0][:2] == ["Nomor", "Tanggal"]
    assert files["journal_entries.csv"][1][2] == "Diposting"
    assert files["journal_entries.csv"][1][4] == "Setoran modal awal"
    assert files["journal_entries.csv"][1][11] == "10000000.00"  # no locale separators in amounts


def test_spreadsheet_formulas_are_neutralized(organization):
    Contact.objects.create(organization=organization, name="=HYPERLINK(\"http://evil\")")
    contacts = export(organization)["contacts.csv"]
    assert contacts[1][0] == "'=HYPERLINK(\"http://evil\")"


def test_owner_downloads_a_zip(auth_client, books, user):
    page = auth_client.get(reverse("reports:export_books"))
    assert page.status_code == 200
    response = auth_client.post(reverse("reports:export_books"))
    assert response.status_code == 200 and response["Content-Type"] == "application/zip"
    assert 'filename="books-' in response["Content-Disposition"]
    files = read_archive(b"".join(response.streaming_content))
    assert user.email in files["README.txt"]


def test_only_managers_may_export(client, make_member, books):
    for role in (Role.VIEWER, Role.ACCOUNTANT):
        client.force_login(make_member(role, email=f"{role}@example.com"))
        assert client.post(reverse("reports:export_books")).status_code == 403


@override_settings(BOOKS_EXPORTS_PER_HOUR=1)
def test_exports_are_throttled_per_organization(auth_client, books):
    assert auth_client.post(reverse("reports:export_books")).status_code == 200
    response = auth_client.post(reverse("reports:export_books"))
    assert response.status_code == 302 and response["Location"] == reverse("reports:export_books")


def test_export_command_writes_a_new_file(books, tmp_path):
    target = tmp_path / "books.zip"
    call_command("export_books", str(books.pk), str(target), "--language", "id")
    assert read_archive(target.read_bytes())["journal_entries.csv"][0][0] == "Nomor"
    with pytest.raises(CommandError, match="already exists"):
        call_command("export_books", str(books.pk), str(target))


def test_delete_requires_an_export_decision(books, tmp_path):
    with pytest.raises(CommandError):
        call_command("delete_organization", str(books.pk), "--yes")
    target = tmp_path / "before-delete.zip"
    call_command("delete_organization", str(books.pk), "--yes", "--export-to", str(target))
    assert len(read_archive(target.read_bytes())["journal_entries.csv"]) == 7
    assert not JournalEntry.objects.filter(organization_id=books.pk).exists()
