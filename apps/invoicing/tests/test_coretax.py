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


# --- Faktur Pajak Keluaran ---------------------------------------------------------

INVOICE_TAGS = [
    "TaxInvoiceDate", "TaxInvoiceOpt", "TrxCode", "AddInfo", "CustomDoc", "CustomDocMonthYear", "RefDesc", "FacilityStamp",
    "SellerIDTKU", "BuyerTin", "BuyerDocument", "BuyerCountry", "BuyerDocumentNumber", "BuyerName", "BuyerAdress",
    "BuyerEmail", "BuyerIDTKU", "ListOfGoodService",
]
GOOD_SERVICE_TAGS = [
    "Opt", "Code", "Name", "Unit", "Price", "Qty", "TotalDiscount", "TaxBase", "OtherTaxBase", "VATRate", "VAT", "STLGRate", "STLG",
]


@pytest.fixture
def sale(books, customer, account):
    customer.npwp, customer.address, customer.city, customer.email = "3174061502560010", "Jl. Merdeka 1", "Bandung", "beli@example.com"
    customer.save()
    ppn = TaxRate.objects.filter(organization=books, dpp_numerator=11).first()
    service = Item.objects.create(organization=books, name="Pelatihan", type=Item.Type.SERVICE, unit="jam")
    draft = services.create_invoice(
        organization=books, kind=InvoiceKind.SALES, contact=customer, date=date(2026, 3, 5),
        lines=[
            InvoiceLineInput("Kopi Arabika", account("4-1000"), D("10"), D("185000"), discount_percent=D("5"), tax_rate=ppn),
            InvoiceLineInput("Pelatihan barista", account("4-1100"), D("2"), D("750000"), tax_rate=ppn, item=service),
            InvoiceLineInput("Ongkos kirim", account("4-2000"), D("1"), D("20000")),  # no PPN: not on the faktur
        ],
    )
    return services.issue_invoice(invoice=draft)


def test_sales_invoices_follow_djp_layout(books, sale):
    content = coretax.build_invoices_xml(books, [sale])
    assert b'xsi:noNamespaceSchemaLocation="TaxInvoice.xsd"' in content
    root = ET.fromstring(content)
    assert root.tag == "TaxInvoiceBulk" and root.findtext("TIN") == "0012345678901234"
    [faktur] = root.find("ListOfTaxInvoice")
    assert [child.tag for child in faktur] == INVOICE_TAGS
    assert [faktur.findtext(t) for t in ("TaxInvoiceDate", "TaxInvoiceOpt", "TrxCode", "RefDesc", "SellerIDTKU")] == [
        "2026-03-05", "Normal", "04", sale.number, "0012345678901234000000",
    ]
    assert [faktur.findtext(t) for t in ("BuyerTin", "BuyerDocument", "BuyerCountry", "BuyerDocumentNumber", "BuyerName", "BuyerAdress", "BuyerEmail", "BuyerIDTKU")] == [
        "3174061502560010", "TIN", "IDN", "-", "PT Pelanggan", "Jl. Merdeka 1, Bandung", "beli@example.com", "3174061502560010000000",
    ]
    kopi, training = faktur.find("ListOfGoodService")
    assert [child.tag for child in kopi] == GOOD_SERVICE_TAGS
    # 10 x 185.000 = 1.850.000 less 5% = 1.757.500; 11/12 = 1.611.041,67; PPN 12% = 193.325.
    assert [kopi.findtext(t) for t in ("Opt", "Unit", "Price", "Qty", "TotalDiscount", "TaxBase", "OtherTaxBase", "VATRate", "VAT")] == [
        "A", "UM.0033", "185000", "10", "92500", "1757500", "1611041.67", "12", "193325",
    ]
    assert [training.findtext(t) for t in ("Opt", "Unit", "TaxBase", "OtherTaxBase", "VAT")] == ["B", "UM.0027", "1500000", "1375000", "165000"]


def test_luxury_goods_use_the_full_tax_base(books, customer, account):
    customer.npwp = "3174061502560010"
    customer.save()
    luxury = TaxRate.objects.get(organization=books, dpp_numerator=1)
    invoice = services.issue_invoice(invoice=services.create_invoice(
        organization=books, kind=InvoiceKind.SALES, contact=customer, date=date(2026, 3, 5),
        lines=[InvoiceLineInput("Jam tangan", account("4-1000"), D("1"), D("10000000"), tax_rate=luxury)],
    ))
    faktur = ET.fromstring(coretax.build_invoices_xml(books, [invoice])).find("ListOfTaxInvoice/TaxInvoice")
    assert faktur.findtext("TrxCode") == "01"
    assert [faktur.findtext(f"ListOfGoodService/GoodService/{t}") for t in ("TaxBase", "OtherTaxBase", "VAT")] == ["10000000", "10000000", "1200000"]


def test_invoices_that_cannot_be_reported_say_why(books, customer, account, sale):
    ppn = TaxRate.objects.filter(organization=books, dpp_numerator=11).first()
    luxury = TaxRate.objects.get(organization=books, dpp_numerator=1)
    customer.npwp = ""
    customer.save()
    no_npwp = services.issue_invoice(invoice=services.create_invoice(
        organization=books, kind=InvoiceKind.SALES, contact=customer, date=date(2026, 3, 5),
        lines=[InvoiceLineInput("Kopi", account("4-1000"), D("1"), D("1000"), tax_rate=ppn)],
    ))
    assert "neither an NPWP nor an identity document" in coretax.invoice_problem(no_npwp)
    customer.npwp = "3174061502560010"
    customer.save()
    mixed = services.issue_invoice(invoice=services.create_invoice(
        organization=books, kind=InvoiceKind.SALES, contact=customer, date=date(2026, 3, 5),
        lines=[InvoiceLineInput("Kopi", account("4-1000"), D("1"), D("1000"), tax_rate=ppn),
               InvoiceLineInput("Jam", account("4-1000"), D("1"), D("1000"), tax_rate=luxury)],
    ))
    assert "separate faktur" in coretax.invoice_problem(mixed)
    untaxed = services.issue_invoice(invoice=services.create_invoice(
        organization=books, kind=InvoiceKind.SALES, contact=customer, date=date(2026, 3, 5),
        lines=[InvoiceLineInput("Kopi", account("4-1000"), D("1"), D("1000"))],
    ))
    assert "no PPN" in coretax.invoice_problem(untaxed)
    books.is_pkp = False
    books.save(update_fields=["is_pkp"])
    sale.refresh_from_db()
    assert "Only a VAT-registered business" in coretax.invoice_problem(sale)


def test_invoice_downloads(auth_client, books, sale):
    response = auth_client.get(reverse("invoicing:coretax", args=[InvoiceKind.SALES, sale.pk]))
    assert response["Content-Type"].startswith("application/xml")
    assert f'filename="faktur-pajak-keluaran-0012345678901234-{sale.number.replace("/", "-")}.xml"' in response["Content-Disposition"]
    page = auth_client.get(reverse("invoicing:detail", args=[InvoiceKind.SALES, sale.pk]))
    assert page.context["show_coretax"] and page.context["coretax_problem"] is None
    month = auth_client.get(reverse("invoicing:month_invoices_coretax", args=[2026, 3]))
    assert len(ET.fromstring(month.content).find("ListOfTaxInvoice")) == 1
    assert auth_client.get(reverse("invoicing:month_invoices_coretax", args=[2026, 4])).status_code == 302
    assert auth_client.get(reverse("reports:vat"), {"year": 2026}).context["sales_months"] == {3}


def test_the_faktur_number_is_recorded_once_after_issue(auth_client, books, sale):
    from django.db import IntegrityError, transaction

    from apps.core.exceptions import InvalidStateTransition, ValidationFailed
    from apps.invoicing.models import Invoice

    with pytest.raises(ValidationFailed, match="17 digits"):
        services.record_tax_invoice_number(invoice=sale, number="123")
    with translation.override("en"):  # the request activates the user's language; keep the test's
        auth_client.post(reverse("invoicing:tax_invoice_number", args=[InvoiceKind.SALES, sale.pk]), {"number": "040.026-00000012345"})
    sale.refresh_from_db()
    assert sale.tax_invoice_number == "04002600000012345"
    with pytest.raises(InvalidStateTransition, match="already has"):
        services.record_tax_invoice_number(invoice=sale, number="04002600000099999")
    with pytest.raises(IntegrityError), transaction.atomic():  # the database keeps it too
        Invoice.objects.filter(pk=sale.pk).update(tax_invoice_number="04002600000099999")
    with pytest.raises(IntegrityError), transaction.atomic():  # and nothing else opens up
        Invoice.objects.filter(pk=sale.pk).update(notes="diubah")


# --- Sales to VAT collectors (codes 02/03) ------------------------------------------


@pytest.fixture
def collector_sale(books, customer, account):
    def make(collector):
        customer.npwp, customer.vat_collector = "0001234567890000", collector
        customer.save()
        ppn = TaxRate.objects.filter(organization=books, dpp_numerator=11).first()
        return services.issue_invoice(invoice=services.create_invoice(
            organization=books, kind=InvoiceKind.SALES, contact=customer, date=date(2026, 3, 5),
            lines=[InvoiceLineInput("Kopi", account("4-1000"), D("10"), D("120000"), tax_rate=ppn)],  # 1.200.000 + PPN 132.000
        ))
    return make


@pytest.mark.parametrize("collector, code", [("government", "02"), ("other", "03")])
def test_sales_to_collectors_use_their_codes_and_keep_dpp_nilai_lain(books, customer, collector_sale, collector, code):
    invoice = collector_sale(collector)
    customer.vat_collector = ""  # the invoice keeps what it was issued with
    customer.save()
    invoice.refresh_from_db()
    faktur = ET.fromstring(coretax.build_invoices_xml(books, [invoice])).find("ListOfTaxInvoice/TaxInvoice")
    assert faktur.findtext("TrxCode") == code
    assert [faktur.findtext(f"ListOfGoodService/GoodService/{t}") for t in ("TaxBase", "OtherTaxBase", "VAT")] == ["1200000", "1100000", "132000"]


def test_the_collector_pays_without_the_ppn(books, account, collector_sale):
    from apps.core.exceptions import ValidationFailed
    from apps.invoicing.models import Invoice
    from apps.invoicing.selectors import vat_summary
    from apps.ledger.selectors import account_balance

    invoice = collector_sale("government")
    with pytest.raises(ValidationFailed, match="at most"):
        services.record_payment(invoice=invoice, date=date(2026, 3, 20), amount=D("1332000"), cash_account=account("1-1100"), vat_withheld=D("140000"))
    services.record_payment(invoice=invoice, date=date(2026, 3, 20), amount=D("1332000"), cash_account=account("1-1100"), vat_withheld=D("132000"))
    invoice.refresh_from_db()
    assert invoice.status == Invoice.Status.PAID and invoice.vat_withheld == D("132000")

    def balance(code):
        return account_balance(books, account(code))

    assert (balance("1-1100"), balance("1-1200"), balance("2-1100")) == (D("1200000"), 0, 0)  # the PPN is the collector's to deposit
    march = vat_summary(books, 2026)[2]
    assert (march["output"], march["collected"], march["net"]) == (D("132000"), D("132000"), 0)


def test_only_collectors_withhold_ppn(books, sale, account):
    from apps.core.exceptions import ValidationFailed

    with pytest.raises(ValidationFailed, match="VAT collector"):
        services.record_payment(invoice=sale, date=date(2026, 3, 20), amount=D("100000"), cash_account=account("1-1100"), vat_withheld=D("1000"))


def test_the_database_refuses_withholding_for_other_buyers(books, sale, account):
    from django.db import IntegrityError, transaction

    from apps.invoicing.models import Payment
    from apps.ledger.services import JournalLineInput, create_journal_entry

    entry = create_journal_entry(
        organization=books, date=date(2026, 3, 20), description="x",
        lines=[JournalLineInput(account("1-1100"), debit=D("10")), JournalLineInput(account("1-1200"), credit=D("10"))],
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        Payment.objects.create(
            organization=books, number="X-1", invoice=sale, date=date(2026, 3, 20), amount=D("10"), vat_withheld=D("5"),
            cash_account=account("1-1100"), journal_entry=entry,
        )


def test_the_payment_form_asks_for_withheld_ppn_only_from_collectors(auth_client, books, sale, collector_sale):
    form = auth_client.get(reverse("invoicing:detail", args=[InvoiceKind.SALES, sale.pk])).context["payment_form"]
    assert "vat_withheld" not in form.fields
    invoice = collector_sale("other")
    form = auth_client.get(reverse("invoicing:detail", args=[InvoiceKind.SALES, invoice.pk])).context["payment_form"]
    assert form.initial["vat_withheld"] == D("132000")


# --- PPN facilities (codes 07/08), buyers without an NPWP, branches ---------------------


def facility_sale(books, customer, account, facility, **kwargs):
    ppn = TaxRate.objects.filter(organization=books, dpp_numerator=11).first()
    return services.issue_invoice(invoice=services.create_invoice(
        organization=books, kind=InvoiceKind.SALES, contact=customer, date=date(2026, 3, 5), facility=facility,
        lines=[InvoiceLineInput("Kopi", account("4-1000"), D("10"), D("120000"), tax_rate=ppn)],  # PPN 132.000, not charged
        **kwargs,
    ))


def test_a_facility_reports_the_ppn_without_charging_it(books, customer, account):
    from apps.invoicing.facilities import VatFacility
    from apps.invoicing.selectors import vat_summary
    from apps.ledger.selectors import account_balance

    customer.npwp = "3174061502560010"
    customer.save()
    facility = services.Facility(VatFacility.NOT_COLLECTED, "TD.00509", "TD.01117", "PJKEK#12345", date(2026, 2, 27))
    invoice = facility_sale(books, customer, account, facility)
    assert (invoice.subtotal, invoice.tax_amount, invoice.vat_not_collected, invoice.total) == (D("1200000"), 0, D("132000"), D("1200000"))
    assert account_balance(books, account("2-1100")) == 0  # no PPN Keluaran: the buyer doesn't pay it
    assert invoice.facility_stamp_text == "PAJAK PERTAMBAHAN NILAI TIDAK DIPUNGUT BERDASARKAN PP NOMOR 40 TAHUN 2021"
    faktur = ET.fromstring(coretax.build_invoices_xml(books, [invoice])).find("ListOfTaxInvoice/TaxInvoice")
    assert [child.tag for child in faktur] == INVOICE_TAGS
    assert [faktur.findtext(t) for t in ("TrxCode", "AddInfo", "CustomDoc", "CustomDocMonthYear", "FacilityStamp")] == [
        "07", "TD.00509", "PJKEK#12345", "022026", "TD.01117",
    ]
    assert [faktur.findtext(f"ListOfGoodService/GoodService/{t}") for t in ("TaxBase", "OtherTaxBase", "VAT")] == ["1200000", "1100000", "132000"]
    march = vat_summary(books, 2026)[2]
    assert (march["output_base"], march["output"], march["not_collected"], march["net"]) == (D("1200000"), 0, D("132000"), 0)

    returned = services.create_return(invoice=invoice, date=date(2026, 3, 9), reason="Rusak", lines=[ReturnLineInput(invoice.lines.get(), D("1"))])
    assert (returned.tax_amount, returned.vat_not_collected, returned.total) == (0, D("13200"), D("120000"))
    assert vat_summary(books, 2026)[2]["not_collected"] == D("118800")


def test_facilities_are_checked(books, customer, account, supplier):
    from apps.core.exceptions import ValidationFailed
    from apps.invoicing.facilities import VatFacility

    with pytest.raises(ValidationFailed, match="code 08"):  # TD.00511 exists only under 07
        facility_sale(books, customer, account, services.Facility(VatFacility.EXEMPT, "TD.00511", "TD.01101"))
    with pytest.raises(ValidationFailed, match="stamp"):
        facility_sale(books, customer, account, services.Facility(VatFacility.EXEMPT, "TD.00507", ""))
    with pytest.raises(ValidationFailed, match="PPN facility first"):
        facility_sale(books, customer, account, services.Facility("", "TD.00507", "TD.01101"))
    with pytest.raises(ValidationFailed, match="keep their PPN rate"):
        services.create_invoice(
            organization=books, kind=InvoiceKind.SALES, contact=customer, date=date(2026, 3, 5),
            facility=services.Facility(VatFacility.EXEMPT, "TD.00507", "TD.01107"),
            lines=[InvoiceLineInput("Air bersih", account("4-1000"), D("1"), D("1000"))],
        )
    with pytest.raises(ValidationFailed, match="sales invoice"):
        services.create_invoice(
            organization=books, kind=InvoiceKind.PURCHASE, contact=supplier, date=date(2026, 3, 5),
            facility=services.Facility(VatFacility.EXEMPT, "TD.00507", "TD.01107"),
            lines=[InvoiceLineInput("Air bersih", account("5-1000"), D("1"), D("1000"))],
        )


def test_the_invoice_form_takes_a_facility(auth_client, books, customer, account):
    ppn = TaxRate.objects.filter(organization=books, dpp_numerator=11).first()
    data = {
        "contact": customer.pk, "date": "2026-03-05", "due_date": "", "reference": "", "tax_invoice_number": "", "notes": "",
        "facility_info_choice": "08|TD.00507", "facility_stamp_choice": "07|TD.01101",
        "supporting_document": "", "supporting_document_date": "",
        "form-TOTAL_FORMS": "1", "form-INITIAL_FORMS": "0", "form-MIN_NUM_FORMS": "1", "form-MAX_NUM_FORMS": "100",
        "form-0-description": "Air bersih", "form-0-account": account("4-1000").pk, "form-0-quantity": "10",
        "form-0-unit_price": "5000", "form-0-discount_percent": "", "form-0-tax_rate": ppn.pk, "form-0-item": "",
    }
    url = reverse("invoicing:create", args=[InvoiceKind.SALES])
    with translation.override("en"):
        response = auth_client.post(url, data)
        assert response.status_code == 200 and list(response.context["form"].errors) == ["facility_stamp_choice"]
        response = auth_client.post(url, {**data, "facility_stamp_choice": "08|TD.01107"})
    assert response.status_code == 302
    from apps.invoicing.models import Invoice

    invoice = Invoice.objects.get(organization=books)
    assert (invoice.vat_facility, invoice.facility_info, invoice.facility_stamp, invoice.tax_amount, invoice.vat_not_collected) == (
        "08", "TD.00507", "TD.01107", 0, D("5499"),  # 50.000 x 11/12 x 12%, rounded down
    )
    form = auth_client.get(reverse("invoicing:update", args=[InvoiceKind.SALES, invoice.pk])).context["form"]
    assert form.initial["facility_info_choice"] == "08|TD.00507" and form.shows_facility


@pytest.mark.parametrize("id_type, number, country", [("National ID", "3174061502560099", "IDN"), ("Passport", "X1234567", "SGP")])
def test_buyers_without_an_npwp_are_identified_by_their_document(books, customer, account, id_type, number, country):
    customer.npwp, customer.id_type, customer.id_number, customer.country = "", id_type, number, country
    customer.full_clean()
    customer.save()
    ppn = TaxRate.objects.filter(organization=books, dpp_numerator=11).first()
    invoice = services.issue_invoice(invoice=services.create_invoice(
        organization=books, kind=InvoiceKind.SALES, contact=customer, date=date(2026, 3, 5),
        lines=[InvoiceLineInput("Kopi", account("4-1000"), D("1"), D("100000"), tax_rate=ppn)],
    ))
    customer.id_number = "changed"  # the invoice keeps the buyer as issued
    customer.save()
    invoice.refresh_from_db()
    faktur = ET.fromstring(coretax.build_invoices_xml(books, [invoice])).find("ListOfTaxInvoice/TaxInvoice")
    assert [faktur.findtext(t) for t in ("BuyerTin", "BuyerDocument", "BuyerCountry", "BuyerDocumentNumber", "BuyerIDTKU")] == [
        "0000000000000000", id_type, country, number, "000000",
    ]


def test_branches_use_their_nitku(books, customer, account):
    from django.core.exceptions import ValidationError

    from apps.tenants.models import BusinessPlace

    branch = BusinessPlace(organization=books, name="Cabang Sleman", nitku="0012345678901234000001", address="Jl. Kaliurang 5", city="Sleman")
    branch.full_clean()
    branch.save()
    customer.npwp, customer.nitku = "3174061502560010", "3174061502560010000002"
    customer.full_clean()
    customer.save()
    ppn = TaxRate.objects.filter(organization=books, dpp_numerator=11).first()

    def sell(**kwargs):
        return services.issue_invoice(invoice=services.create_invoice(
            organization=books, kind=InvoiceKind.SALES, contact=customer, date=date(2026, 3, 5),
            lines=[InvoiceLineInput("Kopi", account("4-1000"), D("1"), D("100000"), tax_rate=ppn)], **kwargs,
        ))

    invoice, head_office = sell(business_place=branch), sell()
    branch.nitku, branch.address = "0012345678901234000009", "Pindah"  # the invoice keeps the place as issued
    branch.save()
    invoice.refresh_from_db()
    faktur, other = ET.fromstring(coretax.build_invoices_xml(books, [invoice, head_office])).find("ListOfTaxInvoice")
    assert (faktur.findtext("SellerIDTKU"), faktur.findtext("BuyerIDTKU")) == ("0012345678901234000001", "3174061502560010000002")
    assert other.findtext("SellerIDTKU") == "0012345678901234000000"
    assert (invoice.seller["address"], invoice.seller["city"]) == ("Jl. Kaliurang 5", "Sleman")
    branch.is_active = False
    branch.save()
    with pytest.raises(Exception, match="inactive"):
        sell(business_place=branch)
    with pytest.raises(ValidationError, match="starts with the 16-digit NPWP"):
        BusinessPlace(organization=books, name="X", nitku="9999999999999999000001").full_clean()

    customer.nitku = "9999999999999999000002"  # someone else's NPWP
    with pytest.raises(ValidationError, match="starts with the 16-digit NPWP"):
        customer.full_clean()
    customer.nitku, customer.id_type, customer.id_number = "", "National ID", "123"
    with pytest.raises(ValidationError, match="16 digits"):
        customer.full_clean()


def test_a_retailers_sales_to_anonymous_buyers_are_reported_in_total(books, customer, account):
    from apps.invoicing.selectors import vat_summary

    customer.npwp = ""
    customer.save()
    ppn = TaxRate.objects.filter(organization=books, dpp_numerator=11).first()
    invoice = services.issue_invoice(invoice=services.create_invoice(
        organization=books, kind=InvoiceKind.SALES, contact=customer, date=date(2026, 3, 5),
        lines=[InvoiceLineInput("Kopi", account("4-1000"), D("2"), D("60000"), tax_rate=ppn)],
    ))
    assert "Coretax directly" in coretax.invoice_problem(invoice)
    assert vat_summary(books, 2026)[2]["aggregated_base"] == 0
    books.is_retailer = True
    books.save(update_fields=["is_retailer"])
    invoice.refresh_from_db()
    assert "digunggung" in coretax.invoice_problem(invoice)
    march = vat_summary(books, 2026)[2]
    assert (march["aggregated_base"], march["aggregated"], march["output"]) == (D("120000"), D("13200"), D("13200"))


def test_the_months_retail_total_as_an_spt_ppn_import(books, customer, account, auth_client):
    customer.npwp = ""
    customer.save()
    books.is_retailer = True
    books.save(update_fields=["is_retailer"])
    ppn = TaxRate.objects.filter(organization=books, dpp_numerator=11).first()
    service = Item.objects.create(organization=books, name="Antar", type=Item.Type.SERVICE, unit="kali")
    invoice = services.issue_invoice(invoice=services.create_invoice(
        organization=books, kind=InvoiceKind.SALES, contact=customer, date=date(2026, 3, 5),
        lines=[
            InvoiceLineInput("Kopi", account("4-1000"), D("2"), D("60000"), tax_rate=ppn),
            InvoiceLineInput("Ongkos antar", account("4-1000"), D("1"), D("10000"), tax_rate=ppn, item=service),
            InvoiceLineInput("Beras", account("4-1000"), D("1"), D("50000")),
        ],
    ))
    rows = coretax.month_retail(books, 2026, 3)
    assert [(r.trx_code, r.goods_or_services, r.base, r.other, r.vat) for r in rows] == [
        ("Normal", "A", D("120000"), D("110000"), D("13200")),
        ("Normal", "B", D("10000"), D("9166.67"), D("1100")),
        ("NoVAT", "A", D("50000"), D("50000"), D("0")),
    ]
    response = auth_client.get(reverse("invoicing:month_retail_coretax", args=[2026, 3]))
    root = ET.fromstring(response.content)
    assert [child.tag for child in root] == ["TIN", "TaxPeriodMonth", "TaxPeriodYear", "ListOfRetailInvoice"]
    first = root.find("ListOfRetailInvoice/RetailInvoice")
    assert [child.tag for child in first] == [
        "TrxCode", "BuyerName", "BuyerIdOpt", "BuyerIdNumber", "GoodServiceOpt", "SerialNo", "TransactionDate",
        "TaxBaseSellingPrice", "OtherTaxBaseSellingPrice", "VAT", "STLG", "Info",
    ]
    assert (first.findtext("BuyerIdNumber"), first.findtext("SerialNo"), first.findtext("Info")) == ("0" * 16, str(invoice.pk), invoice.number)
    second = root.findall("ListOfRetailInvoice/RetailInvoice")[1]
    assert second.findtext("OtherTaxBaseSellingPrice") == "9167"  # whole rupiah (xs:int)
    assert auth_client.get(reverse("invoicing:month_retail_coretax", args=[2026, 4])).status_code == 302
