"""Bank statements and their reconciliation with the books."""

from contextlib import contextmanager
from datetime import date
from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import translation

from apps.banking import services
from apps.banking.models import StatementLine
from apps.banking.selectors import reconciliation
from apps.invoicing import services as invoicing
from apps.invoicing.models import InvoiceKind
from apps.invoicing.services import InvoiceLineInput
from apps.ledger.services import JournalLineInput, create_journal_entry
from apps.tenants.roles import Role
from apps.uploads.domain import RowOutcome
from apps.uploads.importers import IMPORTERS, Context, apply
from apps.uploads.services import PendingFile

D = Decimal


@pytest.fixture(autouse=True)
def english():
    with translation.override("en"):
        yield


@pytest.fixture
def bank(account):
    return account("1-1100")


@pytest.fixture
def statement(organization, user, bank):
    def upload(text, name="mutasi.csv"):
        importer = IMPORTERS["bank_statement"]
        form = importer.options_form({"options-account": str(bank.pk)}, organization=organization, prefix="options")
        assert form.is_valid()
        pending = PendingFile("bank_statement", name, text.encode(), {"options-account": str(bank.pk)})
        return apply(importer, Context(organization, user, form.cleaned_data), pending)

    return upload


@pytest.fixture
def receipt(organization, customer, account):
    invoice = invoicing.issue_invoice(invoice=invoicing.create_invoice(
        organization=organization, kind=InvoiceKind.SALES, contact=customer, date=date(2026, 1, 1),
        lines=[InvoiceLineInput("Jasa", account("4-1100"), D("1"), D("5500000"))],
    ))
    return invoicing.record_payment(invoice=invoice, date=date(2026, 1, 2), amount=D("5500000"), cash_account=account("1-1100"))


def test_a_statement_is_read_in_the_banks_layouts_and_matched(organization, statement, receipt, bank):
    report = statement(
        "Tanggal,Keterangan,Jumlah,Saldo\n"
        '01/01/2026,SALDO AWAL,"0,00",25.000.000\n'  # no amount: refused
    )
    assert not report.ok
    report = statement(
        "Tanggal,Keterangan,Mutasi,Saldo\n"
        "03/01/2026,TRSF E-BANKING CR PT PELANGGAN,\"5,500,000.00 CR\",\"30,500,000.00\"\n"
        "05/01/2026,BIAYA ADM,\"15,000.00 DB\",\"30,485,000.00\"\n"
    )
    assert report.ok, [(r.number, r.message) for r in report.results]
    received, charge = StatementLine.objects.filter(organization=organization).order_by("date")
    assert (received.amount, received.status, received.journal_line.entry_id) == (D("5500000"), StatementLine.Status.MATCHED, receipt.journal_entry_id)
    assert (charge.amount, charge.status, charge.balance) == (D("-15000"), StatementLine.Status.UNMATCHED, D("30485000"))
    overlapping = statement(
        "Tanggal;Keterangan;Debet;Kredit;Saldo\n"
        "05/01/2026;BIAYA ADM;15.000;;30.485.000\n"
        "06/01/2026;BUNGA;;2.500;30.487.500\n", name="mutasi-2.csv",
    )
    assert [r.outcome for r in overlapping.results] == [RowOutcome.SKIPPED, RowOutcome.CREATED]


def test_unrecorded_lines_are_posted_ignored_or_matched_by_hand(organization, statement, bank, account, user):
    statement("date,description,debit,credit\n05/01/2026,BIAYA ADM,15000,\n06/01/2026,KOREKSI BANK,,100\n07/01/2026,SETORAN,,700\n")
    charge, correction, deposit = StatementLine.objects.filter(organization=organization).order_by("date")
    services.post_and_match(line=charge, counter_account=account("7-1000"), description="Biaya administrasi bank", created_by=user)
    charge.refresh_from_db()
    assert charge.status == StatementLine.Status.MATCHED and charge.journal_line.credit == D("15000")
    services.ignore_line(line=correction, ignored_by=user)
    entry = create_journal_entry(organization=organization, date=date(2026, 1, 9), description="Setoran tunai", lines=[
        JournalLineInput(bank, debit=D("700")), JournalLineInput(account("1-1000"), credit=D("700")),
    ])
    services.match_line(line=deposit, journal_line=entry.lines.get(account=bank))
    summary = reconciliation(organization, bank, date(2026, 1, 31))
    assert summary["unrecorded"] == D("0") and summary["books"] == D("-14300")
    services.undo_reconciliation(line=deposit)
    assert reconciliation(organization, bank, date(2026, 1, 31))["unrecorded"] == D("700")


@contextmanager
def refused():
    with pytest.raises(IntegrityError), transaction.atomic():
        yield


def test_statement_lines_stay_as_the_bank_reported_them(organization, statement, bank, account):
    statement("date,description,credit\n03/01/2026,SETORAN,700\n")
    line = StatementLine.objects.get(organization=organization)
    with refused():
        StatementLine.objects.filter(pk=line.pk).update(amount=D("1"))
    with refused():
        line.delete()
    other = create_journal_entry(organization=organization, date=date(2026, 1, 3), description="x", lines=[
        JournalLineInput(bank, debit=D("699")), JournalLineInput(account("1-1000"), credit=D("699")),
    ])
    with refused():  # a different amount
        StatementLine.objects.filter(pk=line.pk).update(status=StatementLine.Status.MATCHED, journal_line=other.lines.get(account=bank))


def test_the_reconciliation_page(auth_client, organization, statement, bank, account, client, make_member, user):
    user.language = "en"
    user.save(update_fields=["language"])
    statement("date,description,debit\n05/01/2026,BIAYA ADM,15000\n")
    line = StatementLine.objects.get(organization=organization)
    page = auth_client.get(reverse("banking:reconciliation", args=[bank.pk]))
    assert page.status_code == 200 and "BIAYA ADM" in page.content.decode()
    response = auth_client.post(reverse("banking:line_action", args=[line.pk, "post"]), {
        "counter_account": account("7-1000").pk, "description": "Biaya admin",
    })
    assert response.status_code == 302
    line.refresh_from_db()
    assert line.status == StatementLine.Status.MATCHED
    assert auth_client.post(reverse("banking:line_action", args=[line.pk, "explode"])).status_code == 404
    client.force_login(make_member(Role.VIEWER))
    assert client.post(reverse("banking:line_action", args=[line.pk, "undo"])).status_code == 403


def test_bank_reconciliation_opens_on_the_account_with_lines_to_reconcile(auth_client, statement, bank):
    statement("date,description,debit\n05/01/2026,BIAYA ADM,15000\n")
    response = auth_client.get(reverse("banking:accounts"))
    assert response.status_code == 302 and response.url == reverse("banking:reconciliation", args=[bank.pk])
    page = auth_client.get(response.url).content.decode()
    assert f'href="{reverse("banking:reconciliation", args=[bank.pk])}"' in page and 'aria-current="page"' in page
