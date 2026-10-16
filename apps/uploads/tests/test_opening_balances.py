"""Balances brought over from the earlier books."""

from datetime import date
from decimal import Decimal

from apps.assets import services as asset_services
from apps.assets.models import FixedAsset
from apps.inventory.selectors import stock_of
from apps.invoicing import services as invoicing
from apps.invoicing.models import Invoice, InvoiceKind
from apps.invoicing.selectors import aging_report, vat_summary
from apps.ledger.domain import JournalSource, SystemRole
from apps.ledger.models import JournalEntry
from apps.ledger.selectors import account_balance
from apps.ledger.services import JournalLineInput, create_journal_entry
from apps.uploads.domain import RowOutcome

from .conftest import messages

D = Decimal
ON = {"on": "2025-12-31"}
TRIAL_BALANCE = (
    "Kode Akun;Nama Akun;Debet;Kredit;Kontak;Nomor Dokumen;Tanggal Dokumen;Jatuh Tempo\n"
    "1-1100;Bank;25.000.000;;;;;\n"
    "1-1200;Piutang Usaha;5.500.000;;PT Pelanggan;INV/2025/0123;15/12/2025;14/01/2026\n"
    "1-1200;Piutang Usaha;1.000.000;;PT Pelanggan;INV/2025/0099;01/11/2025;01/12/2025\n"
    "2-1000;Utang Usaha;;2.500.000;UD Pemasok;F-778;20/12/2025;19/01/2026\n"
    "3-1000;Modal Pemilik;;29.000.000;;;;\n"
)


def test_a_trial_balance_becomes_one_entry_and_the_open_documents(organization, upload, customer, supplier, account, system_account):
    report = upload("opening_balances", TRIAL_BALANCE, options=ON)
    assert report.ok, messages(report)
    on = date(2025, 12, 31)
    assert account_balance(organization, account("1-1100")) == D("25000000")
    assert account_balance(organization, system_account(SystemRole.RECEIVABLE)) == D("6500000")
    assert account_balance(organization, system_account(SystemRole.PAYABLE)) == D("2500000")
    assert account_balance(organization, account("3-1000")) == D("29000000")
    assert account_balance(organization, system_account(SystemRole.OPENING_BALANCE)) == 0  # complete: nothing left over
    entries = JournalEntry.objects.filter(organization=organization)
    assert {e.source for e in entries} == {JournalSource.OPENING} and {e.date for e in entries} == {on}

    sales = Invoice.objects.filter(organization=organization, kind=InvoiceKind.SALES).order_by("due_date")
    assert [(i.reference, i.total, i.due_date, i.opening_balance, i.status) for i in sales] == [
        ("INV/2025/0099", D("1000000"), date(2025, 12, 1), True, Invoice.Status.ISSUED),
        ("INV/2025/0123", D("5500000"), date(2026, 1, 14), True, Invoice.Status.ISSUED),
    ]
    assert "01-11-2025" in sales[0].lines.get().description
    aging = aging_report(organization, InvoiceKind.SALES)
    assert sum(sum(row.buckets.values()) for row in aging.rows) == D("6500000")


def test_open_documents_are_collected_like_invoices_and_stay_out_of_the_tax_files(pkp_organization, upload, customer, supplier, account):
    assert upload("opening_balances", TRIAL_BALANCE, options=ON).ok
    invoice = Invoice.objects.get(organization=pkp_organization, reference="INV/2025/0123")
    invoicing.record_payment(invoice=invoice, date=date(2026, 1, 10), amount=D("5500000"), cash_account=account("1-1100"))
    invoice.refresh_from_db()
    assert invoice.status == Invoice.Status.PAID and not invoice.can_return
    december = vat_summary(pkp_organization, 2025)[11]
    assert december["output_base"] == december["input_base"] == 0
    from apps.invoicing import coretax

    assert not list(coretax.month_invoices(pkp_organization, 2025, 12))
    assert "earlier books" in coretax.invoice_problem(invoice)


def test_the_file_must_balance_and_name_each_documents_party(organization, upload, customer):
    unbalanced = upload("opening_balances", "account code,debit,credit\n1-1100,100,\n3-1000,,90\n", options=ON)
    assert not unbalanced.ok and "don't balance" in unbalanced.problems[0]
    report = upload("opening_balances", (
        "account code,debit,credit,contact\n"
        "1-1200,100,,\n"  # no customer
        "1-1200,100,,Nobody\n"
        "2-1000,50,,\n"  # a debit on payables
        "9-9999,10,,\n"
        "1-1100,10,10,\n"
    ), options=ON)
    assert [o for _n, o, _m in messages(report)] == [RowOutcome.ERROR] * 5
    assert not JournalEntry.objects.filter(organization=organization).exists()
    assert not Invoice.objects.filter(organization=organization).exists()


def test_opening_balances_come_before_the_first_transaction(organization, upload, account):
    create_journal_entry(organization=organization, date=date(2025, 12, 1), description="Setoran", lines=[
        JournalLineInput(account("1-1100"), debit=D("100")), JournalLineInput(account("3-1000"), credit=D("100")),
    ])
    report = upload("opening_balances", "account code,debit,credit\n1-1100,100,\n3-1000,,100\n", options=ON, dry=True)
    assert "before the first transaction" in report.problems[0]


def test_tracked_stock_comes_in_by_item_and_quantity(organization, upload, account):
    item = invoicing.create_item(
        organization=organization, name="Beras", code="BRS", track_inventory=True, inventory_account=account("1-1300"),
        cogs_account=account("5-1000"),
    )
    report = upload("opening_balances", (
        "account code,debit,credit,item code,quantity\n1-1300,1.500.000,,BRS,100\n1-1300,10,,,\n3-1000,,1.500.010,,\n"
    ), options=ON, dry=True)
    assert [o for _n, o, _m in messages(report)] == [RowOutcome.CREATED, RowOutcome.ERROR, RowOutcome.UPDATED]
    report = upload("opening_balances", "account code,debit,credit,item code,quantity\n1-1300,1.500.000,,BRS,100\n3-1000,,1.500.000,,\n", options=ON)
    assert report.ok, messages(report)
    stock = stock_of(item)
    assert (stock.quantity, stock.value) == (D("100"), D("1500000"))
    assert account_balance(organization, account("1-1300")) == D("1500000")


def test_a_partial_file_leaves_the_rest_in_opening_balance_equity(organization, upload, customer, system_account):
    report = upload("opening_balances", "account code,debit,credit,contact\n1-1200,750,,PT Pelanggan\n3-9000,,750,\n", options=ON)
    assert report.ok, messages(report)
    assert account_balance(organization, system_account(SystemRole.OPENING_BALANCE)) == D("750")


def test_fixed_assets_brought_over_are_registered_without_posting(organization, upload, account):
    report = upload("fixed_assets", (
        "Nama Aset,Akun Aset,Tanggal Perolehan,Harga Perolehan,Umur Manfaat (bulan),Akun Akumulasi Penyusutan,Akun Beban Penyusutan,"
        "Disusutkan Sampai,Disusutkan\n"
        "Mobil boks,1-2200,05/03/2023,180.000.000,96,1-2900,6-1600,31/12/2025,\n"
        "Tanah,1-2000,10/01/2020,350.000.000,,,,,tidak\n"
        "Rusak,1-2200,05/03/2023,1000,,,,,\n"  # depreciable without a useful life
    ), dry=True)
    assert [o for _n, o, _m in messages(report)] == [RowOutcome.CREATED, RowOutcome.CREATED, RowOutcome.ERROR]
    report = upload("fixed_assets", (
        "name,asset account,acquisition date,cost,useful life (months),accumulated depreciation account,"
        "depreciation expense account,depreciated through\n"
        "Mobil boks,1-2200,05/03/2023,180.000.000,96,1-2900,6-1600,31/12/2025\n"
    ))
    assert report.ok, messages(report)
    car = FixedAsset.objects.get(organization=organization)
    assert (car.cost, car.useful_life_months, car.depreciated_before_until) == (D("180000000"), 96, date(2025, 12, 31))
    assert not JournalEntry.objects.filter(organization=organization).exists()
    asset_services.post_depreciation(organization=organization, through=date(2026, 1, 31))
    assert car.postings.get().month == 1  # depreciation goes on from January


def test_fixed_assets_need_the_plan_that_has_the_register(organization, auth_client):
    from apps.tenants.domain import PlanFeature

    organization.plan.features = [f for f in PlanFeature.values if f != PlanFeature.FIXED_ASSETS]
    organization.plan.save()
    from django.urls import reverse

    assert auth_client.get(reverse("uploads:upload", args=["fixed-assets"])).status_code == 403
    assert auth_client.get(reverse("uploads:upload", args=["opening-balances"])).status_code == 200

