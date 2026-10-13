"""Coretax XML for purchase returns, checked against DJP's sample (Retur Faktur PM Template v.1.1)."""

from datetime import date
from decimal import Decimal
from xml.etree import ElementTree as ET

import pytest
from django.urls import reverse
from django.utils import translation

from apps.invoicing import coretax, services
from apps.invoicing.models import InvoiceKind, Item
from apps.invoicing.services import InvoiceLineInput, ReturnLineInput
from apps.taxes.models import TaxRate

D = Decimal
# Element order of DJP's sample file; Coretax reads this layout.
DOCUMENT_TAGS = ["InvoiceNumber", "SellerTIN", "ReturnDate", "ReturnTaxBase", "ReturnOtherTaxBase", "ReturnVAT", "ReturnSTLG"]
ROW_TAGS = [
    "Type", "Name", "Code", "Quantity", "Unit", "UnitPrice", "STLGRate", "ReturnQuantity", "ReturnDiscount",
    "ReturnTaxBase", "ReturnOtherTaxBase", "ReturnOtherTaxBaseCheck", "ReturnVAT", "ReturnSTLG",
]
FOOTER_TAGS = ["ReturnTaxBaseTotal", "ReturnOtherTaxBaseTotal", "ReturnVATTotal", "ReturnSTLGTotal"]


@pytest.fixture(autouse=True)
def english():
    with translation.override("en"):
        yield


@pytest.fixture
def books(pkp_organization, supplier, customer, account):
    pkp_organization.npwp = "012345678901234"  # 15 digits: Coretax wants 16
    pkp_organization.save(update_fields=["npwp"])
    supplier.npwp = "1091031210920664"
    supplier.save(update_fields=["npwp"])
    return pkp_organization


@pytest.fixture
def bill(books, supplier, account):
    ppn = TaxRate.objects.filter(organization=books, dpp_numerator=11).first()  # 12%, DPP 11/12
    kopi = Item.objects.create(organization=books, name="Kopi", unit="kg")
    draft = services.create_invoice(
        organization=books, kind=InvoiceKind.PURCHASE, contact=supplier, date=date(2026, 3, 1),
        tax_invoice_number="04002500000348920",
        lines=[
            InvoiceLineInput("Kopi Arabika", account("5-1000"), D("20"), D("100000"), discount_percent=D("10"), tax_rate=ppn, item=kopi),
            InvoiceLineInput("Gula aren", account("5-1000"), D("5"), D("30000"), tax_rate=ppn),
        ],
    )
    return services.issue_invoice(invoice=draft)


def give_back(invoice, quantity="2", on=date(2026, 3, 20)):
    first = invoice.lines.order_by("id").first()
    return services.create_return(invoice=invoice, date=on, reason="Rusak", lines=[ReturnLineInput(first, D(quantity))])


def test_the_file_follows_djp_layout(books, bill):
    note = give_back(bill)
    root = ET.fromstring(coretax.build_xml(books, [note]))
    assert root.tag == "InputTaxInvoiceReturn" and root.findtext("TIN") == "0012345678901234"
    [data] = root.find("InputReturnDataList")
    document = data.find("TransactionDocumentData")
    assert [child.tag for child in document] == DOCUMENT_TAGS
    rows = data.find("TransactionDetailsData").findall("Rows")
    assert all([child.tag for child in row] == ROW_TAGS for row in rows)
    assert [child.tag for child in data.find("TransactionDetailsData/FooterRow")] == FOOTER_TAGS

    assert document.findtext("InvoiceNumber") == "04002500000348920"
    assert document.findtext("SellerTIN") == "1091031210920664"
    assert document.findtext("ReturnDate") == "20-03-2026"
    # 2 kg x 100.000 less 10% = 180.000; DPP nilai lain 11/12 = 165.000; PPN 12% = 19.800.
    assert [document.findtext(t) for t in ("ReturnTaxBase", "ReturnOtherTaxBase", "ReturnVAT", "ReturnSTLG")] == ["180000", "165000", "19800", "0"]

    kopi, gula = rows  # every line of the faktur, the one not returned with 0
    assert [kopi.findtext(t) for t in ("Type", "Name", "Quantity", "Unit", "UnitPrice", "ReturnQuantity", "ReturnDiscount", "ReturnOtherTaxBaseCheck")] == [
        "A", "Kopi Arabika", "20", "UM.0003", "100000", "2", "20000", "true",
    ]
    assert [gula.findtext(t) for t in ("Name", "Unit", "ReturnQuantity", "ReturnTaxBase", "ReturnVAT")] == ["Gula aren", "UM.0033", "0", "0", "0"]
    assert data.findtext("TransactionDetailsData/FooterRow/ReturnVATTotal") == "19800"


@pytest.mark.parametrize("change, message", [
    ("sales", "Your customer records a sales return"),
    ("not_pkp", "Only a VAT-registered business"),
    ("no_faktur", "no Faktur Pajak number"),
    ("void", "voided return"),
])
def test_only_reportable_returns_are_exported(books, bill, customer, account, change, message):
    if change == "sales":
        sale = services.issue_invoice(invoice=services.create_invoice(
            organization=books, kind=InvoiceKind.SALES, contact=customer, date=date(2026, 3, 1),
            lines=[InvoiceLineInput("Kopi", account("4-1000"), D("1"), D("1000"))],
        ))
        note = give_back(sale, "1")
    else:
        note = give_back(bill)
    if change == "not_pkp":
        books.is_pkp = False
        books.save(update_fields=["is_pkp"])
        note.refresh_from_db()
    if change == "no_faktur":
        draft = services.create_invoice(
            organization=books, kind=InvoiceKind.PURCHASE, contact=bill.contact, date=date(2026, 3, 1),
            lines=[InvoiceLineInput("Kopi", account("5-1000"), D("1"), D("1000"), tax_rate=bill.lines.first().tax_rate)],
        )
        note = give_back(services.issue_invoice(invoice=draft), "1")
    if change == "void":
        services.void_return(note=note, on=date(2026, 3, 21))
        note.refresh_from_db()
    assert message in coretax.exportable_problem(note)


def test_downloads(auth_client, books, bill):
    note = give_back(bill)
    give_back(bill, "1", on=date(2026, 3, 25))
    response = auth_client.get(reverse("invoicing:return_coretax", args=[note.pk]))
    assert response["Content-Type"].startswith("application/xml")
    assert f'filename="retur-pajak-masukan-0012345678901234-{note.number.replace("/", "-")}.xml"' in response["Content-Disposition"]

    month = auth_client.get(reverse("invoicing:month_coretax", args=[2026, 3]))
    assert len(ET.fromstring(month.content).find("InputReturnDataList")) == 2
    empty = auth_client.get(reverse("invoicing:month_coretax", args=[2026, 4]))
    assert empty.status_code == 302 and "year=2026" in empty.url
    assert auth_client.get(reverse("reports:vat"), {"year": 2026}).context["return_months"] == {3}


@pytest.mark.parametrize("unit, code", [("kg", "UM.0003"), (" PCS ", "UM.0021"), ("Lusin", "UM.0017"), ("karung", "UM.0033")])
def test_units_map_to_djp_codes(unit, code):
    assert coretax.unit_code(unit) == code
