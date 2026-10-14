"""Imports, exports and other tax documents: exports with a PEB and bills equivalent to a faktur
(Coretax "Dokumen Lain"), import declarations (PIB), PPh 26 on payments abroad (BPNR),
self-deposited final PPh (BPSP), and returns of retail sales reported in total."""

from datetime import date
from decimal import Decimal
from xml.etree import ElementTree as ET

import pytest
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import translation

from apps.contacts.models import Contact
from apps.core.exceptions import InvalidStateTransition, ValidationFailed
from apps.invoicing import coretax
from apps.invoicing import services as invoicing
from apps.invoicing.coretax_api import DocumentType
from apps.invoicing.models import IncomeTaxWithheld, Invoice, InvoiceKind, SpecialDocument
from apps.invoicing.selectors import vat_summary
from apps.invoicing.services import IncomeTaxInput, InvoiceLineInput, ReturnLineInput, SpecialDoc
from apps.ledger.selectors import account_balance
from apps.taxes import ebupot, services
from apps.taxes.models import TaxRate
from apps.taxes.selectors import withholding_due

D = Decimal


@pytest.fixture(autouse=True)
def english():
    with translation.override("en"):
        yield


@pytest.fixture
def books(pkp_organization):
    pkp_organization.npwp = "0123456789012345"
    pkp_organization.save(update_fields=["npwp"])
    return pkp_organization


@pytest.fixture
def ppn(books):
    return TaxRate.objects.filter(organization=books, dpp_numerator=11).first()  # 12% on DPP 11/12


@pytest.fixture
def abroad(books):
    return Contact.objects.create(
        organization=books, kind=Contact.Kind.SUPPLIER, name="Acme Pte Ltd", country="SGP", address="1 Raffles Place",
        foreign_tin="SG-200012345K",
    )


def children(element):
    return [child.tag for child in element]


def test_an_export_is_reported_with_its_peb(books, customer, account, ppn):
    with pytest.raises(ValidationFailed, match="0%"):
        invoicing.create_invoice(
            organization=books, kind=InvoiceKind.SALES, contact=customer, date=date(2026, 3, 5),
            lines=[InvoiceLineInput("Kopi", account("4-1000"), D("10"), D("100000"), tax_rate=ppn)],
            special=SpecialDoc(SpecialDocument.EXPORT_GOODS),
        )
    invoice = invoicing.issue_invoice(invoice=invoicing.create_invoice(
        organization=books, kind=InvoiceKind.SALES, contact=customer, date=date(2026, 3, 5),
        lines=[InvoiceLineInput("Kopi", account("4-1000"), D("10"), D("100000"))], special=SpecialDoc(SpecialDocument.EXPORT_GOODS),
    ))
    assert "PEB" in coretax.invoice_problem(invoice)
    rows, problems = coretax.month_exports(books, 2026, 3)
    assert rows == [] and invoice.number in problems[0]
    invoicing.record_special_document(invoice=invoice, number="000123", on=date(2026, 4, 2))
    with pytest.raises(InvalidStateTransition, match="already has"):
        invoicing.record_special_document(invoice=invoice, number="X", on=date(2026, 4, 2))
    with pytest.raises(IntegrityError), transaction.atomic():
        Invoice.objects.filter(pk=invoice.pk).update(special_document_number="X")
    assert coretax.month_exports(books, 2026, 3) == ([], [])
    rows, _problems = coretax.month_exports(books, 2026, 4)  # reported in the PEB's month
    root = ET.fromstring(coretax.build_exports_xml(books, rows))
    (doc,) = root.find("ListOfSpecialDoc")
    assert children(doc) == [
        "TransactionType", "TransactionDetail", "TransactionDocument", "AdditionalInformation", "DocumentNumber", "DocumentDate",
        "Reference", "BuyerTIN", "BuyerName", "BuyerAddress", "TaxBase", "VAT", "STLG",
    ]
    assert [doc.findtext(t) for t in ("TransactionType", "TransactionDetail", "TransactionDocument", "DocumentNumber", "TaxBase", "VAT")] == [
        "02", "11", "4", "000123", "1000000", "0"]


def test_a_bill_equivalent_to_a_faktur_and_its_return(books, supplier, account, ppn, auth_client):
    with pytest.raises(ValidationFailed, match="no Faktur Pajak number"):
        invoicing.create_invoice(
            organization=books, kind=InvoiceKind.PURCHASE, contact=supplier, date=date(2026, 3, 10), tax_invoice_number="04002600000012345",
            lines=[InvoiceLineInput("Listrik", account("6-1200"), D("1"), D("1200000"), tax_rate=ppn)],
            special=SpecialDoc(SpecialDocument.EQUIVALENT, "PLN-123", date(2026, 3, 10)),
        )
    bill = invoicing.issue_invoice(invoice=invoicing.create_invoice(
        organization=books, kind=InvoiceKind.PURCHASE, contact=supplier, date=date(2026, 3, 10),
        lines=[InvoiceLineInput("Listrik", account("6-1200"), D("2"), D("600000"), tax_rate=ppn)],
        special=SpecialDoc(SpecialDocument.EQUIVALENT, "PLN-123", date(2026, 3, 10)),
    ))
    rows, problems = coretax.month_special_inputs(books, 2026, 3)
    assert problems == [] and [(r.number, r.trx_type, r.trx_code, r.trx_document, r.base, r.vat) for r in rows] == [
        ("PLN-123", "04", "01", "8", D("1100000"), D("132000"))]
    root = ET.fromstring(coretax.build_special_inputs_xml(books, rows))
    assert children(root.find("ListOfSpecialDoc")[0]) == [
        "SpecialDocNo", "SpecialDocDate", "TrxType", "TrxCode", "TrxDocument", "TaxPeriodMonth", "TaxPeriodYear", "TotalTaxBase",
        "TotalVAT", "TotalSTLG", "SellerTin", "SellerName",
    ]
    note = invoicing.create_return(invoice=bill, date=date(2026, 3, 20), reason="Salah tagih", lines=[ReturnLineInput(bill.lines.get(), D("1"))])
    assert "Retur Dokumen Lain Masukan" in coretax.exportable_problem(note)
    (returned,) = coretax.month_special_returns(books, 2026, 3)
    data = ET.fromstring(coretax.build_special_returns_xml(books, [returned])).find("InputReturnDataList/InputReturnData")
    assert children(data) == [
        "DocumentNumber", "ReturnDocumentNumber", "TransactionType", "DocumentType", "TransactionDetail", "TransactionDocument",
        "DocumentDate", "Period", "Year", "SellerTIN", "SellerTaxpayerName", "ReturnTaxBase", "ReturnVAT", "ReturnSTLG",
    ]
    assert [data.findtext(t) for t in ("DocumentNumber", "TransactionType", "TransactionDocument", "ReturnTaxBase", "ReturnVAT")] == [
        "PLN-123", "04", "08", "550000", "66000"]
    page = auth_client.get(reverse("reports:vat"), {"year": 2026})
    (march,) = page.context["special_months"]
    assert (march.month, march.inputs, march.returns) == (3, 1, 1)
    xml = auth_client.get(reverse("invoicing:month_special_documents", args=[2026, 3, "masukan"]))
    assert xml.status_code == 200 and b"PLN-123" in xml.content


def test_an_import_declaration(books, account, auth_client):
    declaration = services.record_import_declaration(
        organization=books, number="000555", on=date(2026, 3, 12), supplier_name="Acme Pte Ltd", customs_value=D("50000000"),
        import_duty=D("2500000"), vat=D("5500000"), luxury_tax=D("0"), pph22=D("1250000"), cost_account=account("5-1000"),
        cash_account=account("1-1100"), prepaid_tax_account=account("1-1500"), ntpn="ABCD1234EFGH5678",
    )
    assert account_balance(books, account("1-1100")) == -(D("2500000") + D("5500000") + D("1250000"))
    assert account_balance(books, account("1-1500")) == D("1250000")
    march = vat_summary(books, 2026)[2]
    assert (march["input"], march["input_base"]) == (D("5500000"), D("50000000"))
    rows, _problems = coretax.month_special_inputs(books, 2026, 3)
    assert [(r.trx_type, r.trx_code, r.trx_document, r.seller_tin, r.vat) for r in rows] == [("03", "14", "6", "0" * 16, D("5500000"))]
    with pytest.raises(ValidationFailed, match="already been recorded"):
        services.record_import_declaration(
            organization=books, number="000555", on=date(2026, 3, 12), supplier_name="Acme", customs_value=D("1"), import_duty=D("0"),
            vat=D("1"), luxury_tax=D("0"), pph22=D("0"), cost_account=account("5-1000"), cash_account=account("1-1100"),
        )
    assert auth_client.get(reverse("taxes:imports"), {"year": 2026}).status_code == 200
    services.cancel_import_declaration(declaration=declaration, on=date(2026, 3, 31))
    assert vat_summary(books, 2026)[2]["input"] == 0 and account_balance(books, account("1-1100")) == 0


def test_pph26_on_a_payment_abroad(books, abroad, account):
    bill = invoicing.issue_invoice(invoice=invoicing.create_invoice(
        organization=books, kind=InvoiceKind.PURCHASE, contact=abroad, date=date(2026, 3, 1),
        lines=[InvoiceLineInput("Konsultasi", account("6-1700"), D("1"), D("10000000"))],
    ))
    with pytest.raises(ValidationFailed, match="withheld at 20%"):  # a lower rate needs the certificate of residence
        invoicing.record_payment(invoice=bill, date=date(2026, 3, 5), amount=D("10000000"), cash_account=account("1-1100"),
                                 income_tax=IncomeTaxInput(IncomeTaxWithheld.PPH_26, base=D("10000000"), rate=D("10"), object_code="27-104-01"))
    abroad.treaty_document = "DGT-SGP-2026"
    abroad.save()
    payment = invoicing.record_payment(
        invoice=bill, date=date(2026, 3, 5), amount=D("10000000"), cash_account=account("1-1100"),
        income_tax=IncomeTaxInput(IncomeTaxWithheld.PPH_26, base=D("10000000"), rate=D("10"), object_code="27-104-01"),
    )
    assert payment.income_tax_withheld == D("1000000") and account_balance(books, account("2-1250")) == D("1000000")
    assert withholding_due(books, 2026) == {("pph26", 3): D("1000000")}
    (row,) = ebupot.month_batches(books, 2026, 3)[DocumentType.BPNR].records
    fields = dict(row.fields)
    assert (fields["CounterpartTin"], fields["TaxCertificate"], fields["CounterpartReceiptNumber"], fields["Deemed"], fields["Rate"]) == (
        "SG-200012345K", "COD", "DGT-SGP-2026", D("100"), D("10"))


def test_self_deposited_final_pph_on_rent(books, customer, account):
    deposit = services.record_self_deposit(
        organization=books, tax_object="28-403-02", tax_base=D("5000000"), document_number="INV/2026/03/0007",
        document_date=date(2026, 3, 1), paid_on=date(2026, 4, 10), cash_account=account("1-1100"),
    )
    assert deposit.amount == D("500000") and (deposit.year, deposit.month) == (2026, 3)
    assert account_balance(books, account("2-1220")) == 0  # the expense's payable, settled by the deposit
    assert account_balance(books, account("1-1100")) == D("-500000")
    (record,) = ebupot.month_batches(books, 2026, 3)[DocumentType.BPSP].records
    assert [tag for tag, _value in record.fields][:5] == ["TaxPeriodMonth", "TaxPeriodYear", "TaxCertificate", "TaxObjectCode", "TaxBase"]
    services.record_self_deposit_slip(deposit=deposit, number="BPSP-1")
    assert DocumentType.BPSP not in ebupot.month_batches(books, 2026, 3)
    services.cancel_self_deposit(deposit=deposit, on=date(2026, 4, 30))
    assert account_balance(books, account("1-1100")) == 0


def test_returns_of_retail_sales_reduce_the_month_of_the_return(books, customer, account, ppn):
    customer.npwp = ""
    customer.save()
    books.is_retailer = True
    books.save(update_fields=["is_retailer"])
    invoice = invoicing.issue_invoice(invoice=invoicing.create_invoice(
        organization=books, kind=InvoiceKind.SALES, contact=customer, date=date(2026, 3, 5),
        lines=[InvoiceLineInput("Kopi", account("4-1000"), D("2"), D("60000"), tax_rate=ppn)],
    ))
    invoicing.create_return(invoice=invoice, date=date(2026, 4, 2), reason="Rusak", lines=[ReturnLineInput(invoice.lines.get(), D("1"))])
    (row,) = coretax.month_retail(books, 2026, 4)
    assert (row.trx_code, row.base, row.other, row.vat, row.info.startswith("Retur")) == ("Normal", D("-60000"), D("-55000"), D("-6600"), True)
