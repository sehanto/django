"""What Coretax gives back: Faktur Pajak numbers, input VAT fakturs and bukti potong numbers."""

from datetime import date
from decimal import Decimal

import pytest

from apps.invoicing import services
from apps.invoicing.models import IncomeTaxWithheld, InvoiceKind
from apps.invoicing.services import IncomeTaxInput, InvoiceLineInput
from apps.taxes.models import TaxRate
from apps.uploads.domain import RowOutcome

from .conftest import messages

D = Decimal
ON = date(2026, 3, 10)


@pytest.fixture
def ppn(organization):
    return TaxRate.objects.get(organization=organization, name__startswith="PPN 12% (DPP 11/12)")


@pytest.fixture
def invoice(pkp_organization, customer, account, ppn):
    customer.npwp = "012345678901000"
    customer.save()
    return services.issue_invoice(invoice=services.create_invoice(
        organization=pkp_organization, kind=InvoiceKind.SALES, contact=customer, date=ON,
        lines=[InvoiceLineInput("Kopi", account("4-1000"), D("10"), D("100000"), tax_rate=ppn)],
    ))


@pytest.fixture
def bill(pkp_organization, supplier, account, ppn):
    supplier.npwp = "0098765432109000"
    supplier.save()

    def make(on=ON, price="1000000"):
        return services.issue_invoice(invoice=services.create_invoice(
            organization=pkp_organization, kind=InvoiceKind.PURCHASE, contact=supplier, date=on,
            lines=[InvoiceLineInput("Bahan", account("5-1000"), D("1"), D(price), tax_rate=ppn)],
        ))

    return make


def test_faktur_numbers_are_recorded_by_the_reference_or_buyer_date_and_ppn(invoice, upload, customer, account, ppn):
    other = services.issue_invoice(invoice=services.create_invoice(
        organization=invoice.organization, kind=InvoiceKind.SALES, contact=customer, date=ON,
        lines=[InvoiceLineInput("Teh", account("4-1000"), D("1"), D("200000"), tax_rate=ppn)],
    ))
    report = upload("output_fakturs", (
        "Nomor Faktur Pajak;Referensi;NPWP Pembeli;Tanggal Faktur Pajak;PPN;Status Faktur\n"
        f"04002600000012345;{invoice.number};0012345678901000;10/03/2026;110.000;APPROVED\n"
        "04002600000012346;;0012345678901000;10/03/2026;21999;APPROVED\n"
        "04002600000012347;INV/LAIN;0012345678901000;11/03/2026;5000;APPROVED\n"
        "04002600000012348;;;;;CANCELED\n"
    ))
    assert report.ok, messages(report)
    assert [o for _n, o, _m in messages(report)] == [RowOutcome.UPDATED, RowOutcome.UPDATED, RowOutcome.SKIPPED, RowOutcome.SKIPPED]
    invoice.refresh_from_db()
    other.refresh_from_db()
    assert (invoice.tax_invoice_number, other.tax_invoice_number) == ("04002600000012345", "04002600000012346")


def test_a_faktur_that_disagrees_with_the_invoice_is_not_recorded(invoice, upload):
    report = upload("output_fakturs", f"faktur number,reference,PPN\n04002600000099999,{invoice.number},1\n")
    assert "differs" in messages(report)[0][2]
    services.record_tax_invoice_number(invoice=invoice, number="04002600000012345")
    report = upload("output_fakturs", f"faktur number,reference\n04002600000099999,{invoice.number}\n0400260000001234,{invoice.number}\n")
    assert "already has" in messages(report)[0][2]
    assert messages(report)[1][1] == RowOutcome.SKIPPED
    bad = upload("output_fakturs", "faktur number\n123\n", dry=True)
    assert messages(bad)[0][1] == RowOutcome.ERROR


def test_input_fakturs_are_matched_to_bills_and_the_gaps_are_listed(bill, upload):
    matched, unmatched = bill(), bill(on=date(2026, 3, 20), price="500000")
    report = upload("input_fakturs", (
        "NPWP Penjual,Nama Penjual,Nomor Faktur Pajak,Tanggal Faktur Pajak,DPP,PPN\n"
        "0098765432109000,UD Pemasok,04002600000054321,12/03/2026,1000000,110000\n"
        "0098765432109000,UD Pemasok,04002600000054322,12/03/2026,700000,77000\n"
    ))
    assert report.ok, messages(report)
    assert [o for _n, o, _m in messages(report)] == [RowOutcome.UPDATED, RowOutcome.SKIPPED]
    assert "No purchase bill" in messages(report)[1][2]
    matched.refresh_from_db()
    assert matched.tax_invoice_number == "04002600000054321"
    assert len(report.notes) == 1 and unmatched.number in report.notes[0]


@pytest.fixture
def withheld(organization, supplier, account):
    supplier.npwp = "0098765432109000"
    supplier.save()
    bill = services.issue_invoice(invoice=services.create_invoice(
        organization=organization, kind=InvoiceKind.PURCHASE, contact=supplier, date=ON,
        lines=[InvoiceLineInput("Sewa alat", account("6-1100"), D("1"), D("5000000"))],
    ))
    return services.record_payment(
        invoice=bill, date=date(2026, 3, 20), amount=D("5000000"), cash_account=account("1-1100"),
        income_tax=IncomeTaxInput(IncomeTaxWithheld.PPH_23, base=D("5000000"), rate=D("2"), object_code="24-104-01"),
    )


def test_bukti_potong_numbers_are_recorded_on_the_payments(withheld, upload):
    report = upload("withholding_slips", (
        "Nomor Bukti Potong,NPWP/NIK Penerima Penghasilan,Nomor Dokumen,Kode Objek Pajak,Penghasilan Bruto,Masa Pajak,Tahun Pajak,Status\n"
        f"2600123456,0098765432109000,{withheld.invoice.number},24-104-01,5.000.000,3,2026,NORMAL\n"
        f"2600123457,0011111111111000,{withheld.invoice.number},24-104-01,5.000.000,3,2026,NORMAL\n"
    ))
    assert report.ok, messages(report)
    assert [o for _n, o, _m in messages(report)] == [RowOutcome.UPDATED, RowOutcome.SKIPPED]
    withheld.refresh_from_db()
    assert withheld.withholding_slip == "2600123456"
    again = upload("withholding_slips", f"bukti potong number,document number\n2600123456,{withheld.invoice.number}\n", dry=True)
    assert messages(again)[0][2] == "Already recorded."
