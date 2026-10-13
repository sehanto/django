from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.urls import reverse

from apps.invoicing import services as invoicing
from apps.invoicing.models import InvoiceKind
from apps.invoicing.services import InvoiceLineInput
from apps.ledger.selectors import account_balances
from apps.ledger.services import JournalLineInput, create_journal_entry
from apps.reports.forms import one_year_earlier
from apps.reports.statements import build_balance_sheet, build_income_statement, build_trial_balance
from apps.reports.views import balance_sheet, income_statement
from apps.taxes.models import TaxRate

D = Decimal


@pytest.fixture
def books(pkp_organization, account, customer, supplier):
    """A small but realistic set of transactions across two years."""
    org = pkp_organization
    ppn = TaxRate.objects.filter(organization=org).first()

    def journal(on, description, *lines):
        create_journal_entry(organization=org, date=on, description=description, lines=list(lines))

    journal(date(2025, 1, 2), "Modal awal",
            JournalLineInput(account("1-1100"), debit=D("50000000")), JournalLineInput(account("3-1000"), credit=D("50000000")))
    journal(date(2025, 6, 1), "Penjualan tunai 2025",
            JournalLineInput(account("1-1000"), debit=D("8000000")), JournalLineInput(account("4-1000"), credit=D("8000000")))
    journal(date(2025, 6, 2), "Pembelian peralatan",
            JournalLineInput(account("1-2300"), debit=D("12000000")), JournalLineInput(account("1-1100"), credit=D("12000000")))
    journal(date(2025, 12, 31), "Penyusutan",
            JournalLineInput(account("6-1600"), debit=D("1000000")), JournalLineInput(account("1-2900"), credit=D("1000000")))
    journal(date(2026, 1, 5), "Prive",
            JournalLineInput(account("3-2000"), debit=D("500000")), JournalLineInput(account("1-1000"), credit=D("500000")))

    invoice = invoicing.create_invoice(
        organization=org, kind=InvoiceKind.SALES, contact=customer, date=date(2026, 2, 1),
        lines=[InvoiceLineInput("Jasa", account("4-1100"), D("1"), D("10000000"), tax_rate=ppn)],
    )
    invoice = invoicing.issue_invoice(invoice=invoice)
    invoicing.record_payment(invoice=invoice, date=date(2026, 2, 10), amount=D("5000000"), cash_account=account("1-1100"))

    bill = invoicing.create_invoice(
        organization=org, kind=InvoiceKind.PURCHASE, contact=supplier, date=date(2026, 2, 3),
        lines=[InvoiceLineInput("Bahan", account("5-1000"), D("1"), D("4000000"), tax_rate=ppn)],
    )
    invoicing.issue_invoice(invoice=bill)
    return org


def test_trial_balance_balances(books):
    report = build_trial_balance(account_balances(books, date_to=date(2026, 12, 31)), date(2026, 12, 31))
    assert report.is_balanced
    assert report.total_debit > 0


def test_income_statement_for_current_year(books):
    balances = account_balances(books, date_from=date(2026, 1, 1), date_to=date(2026, 12, 31))
    report = build_income_statement(balances, date(2026, 1, 1), date(2026, 12, 31))
    assert report.revenue.total == D("10000000")
    assert report.cost_of_sales.total == D("4000000")
    assert report.gross_profit == D("6000000")
    assert report.net_profit == D("6000000")


def test_balance_sheet_balances_and_splits_earnings(books):
    as_of = date(2026, 12, 31)
    report = build_balance_sheet(
        account_balances(books, date_to=as_of),
        account_balances(books, date_to=date(2026, 1, 1) - timedelta(days=1)),
        as_of,
    )
    assert report.is_balanced, (report.total_assets, report.total_liabilities_and_equity)
    assert report.prior_years_earnings == D("7000000")  # 8.000.000 sales - 1.000.000 depreciation
    assert report.current_year_earnings == D("6000000")
    # Contra accounts reduce their sections.
    fixed = {r.code: r.amount for r in report.fixed_assets.rows}
    assert fixed["1-2900"] == D("-1000000")
    assert report.fixed_assets.total == D("11000000")
    equity = {r.code: r.amount for r in report.equity.rows}
    assert equity["3-2000"] == D("-500000")
    # PPN: output 1.100.000 (liability) and input 440.000 (asset).
    assert {r.code: r.amount for r in report.current_liabilities.rows}["2-1100"] == D("1100000")
    assert {r.code: r.amount for r in report.current_assets.rows}["1-1400"] == D("440000")


# --- SAK EMKM presentation: comparatives and notes ---------------------------------


def test_income_statement_compares_with_the_same_period_a_year_earlier(books):
    report = income_statement(books, date(2026, 1, 1), date(2026, 12, 31))
    assert (report.previous_from, report.previous_to) == (date(2025, 1, 1), date(2025, 12, 31))
    assert report.revenue.total == D("10000000") and report.revenue.previous_total == D("8000000")
    assert report.net_profit == D("6000000") and report.previous_net_profit == D("7000000")
    sales = {r.code: (r.amount, r.previous) for r in report.revenue.rows}
    assert sales == {"4-1000": (D("0"), D("8000000")), "4-1100": (D("10000000"), D("0"))}  # in either period


def test_balance_sheet_compares_with_the_previous_year_end(books):
    report = balance_sheet(books, date(2026, 6, 30))
    assert report.previous_as_of == date(2025, 12, 31)
    assert report.is_balanced  # both columns
    assert report.previous_current_year_earnings == D("7000000") and report.previous_prior_years_earnings == 0
    assert report.prior_years_earnings == D("7000000")
    assert report.previous_total_assets == D("57000000")  # capital 50 jt + sales 8 jt - depreciation 1 jt


@pytest.fixture
def english(user):
    user.language = "en"
    user.save(update_fields=["language"])


def test_comparative_csv_has_both_columns(auth_client, books, english):
    response = auth_client.get(reverse("reports:income_statement"), {"date_from": "2026-01-01", "date_to": "2026-12-31", "download": "csv"})
    header = response.content.decode("utf-8-sig").splitlines()[0].split(";")
    assert header[2:] == ["01-01-2026 to 31-12-2026", "01-01-2025 to 31-12-2025"]


@pytest.mark.parametrize(("day", "expected"), [(date(2028, 2, 29), date(2027, 2, 28)), (date(2026, 3, 31), date(2025, 3, 31))])
def test_one_year_earlier(day, expected):
    assert one_year_earlier(day) == expected


def test_notes_state_compliance_policies_and_explain_each_line(auth_client, books, english):
    books.inventory_cost_formula = books.InventoryCostFormula.FIFO
    books.additional_notes = "Jaminan kredit bank: <b>sertifikat</b> toko."
    books.save(update_fields=["inventory_cost_formula", "additional_notes"])
    page = auth_client.get(reverse("reports:notes"), {"as_of": "2026-12-31"}).content.decode()
    assert "SAK EMKM" in page and "accrual basis" in page
    assert "First in, first out (FIFO)" in page and "Straight line" in page
    assert "PPN charged to customers" in page  # a PKP
    assert "<h2 class=\"h6 fw-bold\">3. Current assets</h2>" in page
    assert "&lt;b&gt;sertifikat&lt;/b&gt;" in page  # user text stays escaped


def test_notes_describe_the_tax_scheme(auth_client, organization, english):
    page = auth_client.get(reverse("reports:notes")).content.decode()
    assert "PP 55/2022" in page and "no deferred tax" in page
    organization.uses_pph_final = False
    organization.save(update_fields=["uses_pph_final"])
    page = auth_client.get(reverse("reports:notes")).content.decode()
    assert "PP 55/2022" not in page and "no deferred tax is recognised" in page
    assert "None. Add commitments" in page
