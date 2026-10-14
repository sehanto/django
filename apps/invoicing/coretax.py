"""Coretax DJP XML import files: Faktur Pajak Keluaran for sales invoices, and nota retur
("Retur Pajak Masukan") for purchase returns.

Both follow DJP's samples and Excel templates shipped with converter v1.6 (pajak.go.id,
Template XML dan Converter Excel ke XML).

Sales invoices ("Sample Faktur PK Template v.1.4", rules from template v.1.6.1): one
TaxInvoice per invoice, dated YYYY-MM-DD, "Normal", and one GoodService per taxed line.
Transaction code, in DJP's order of priority (``transaction_code``): 07/08 for a delivery
under a PPN facility (with its keterangan tambahan, cap fasilitas and supporting document),
02/03 when the buyer is a VAT collector (government treasurer / other appointed collector,
from the contact as it was at issue), 06 for a tourist's VAT refund, 05 for PPN besaran
tertentu, 09 for an asset not originally for sale (Art. 16D), 10 for a rate other than 12%,
else 04 when the lines use DPP nilai lain (11/12, the standard 12% rate since 2025) or 01 with
the full DPP (luxury goods). PPnBM goes in STLGRate/STLG on the same base as the PPN. The
seller's NITKU is that of the place of business the invoice was made from, the buyer's its
branch's when set, else the 16-digit NPWP plus 000000 (head office). A buyer without an NPWP is
identified by its NIK, passport or other document: NPWP 0000000000000000, its country and
NITKU 000000, as the template asks. Coretax issues the faktur number; record it on the
invoice afterwards.

Purchase returns: since 2025 the buyer creates the nota retur in Coretax DJP and the seller
approves it, so only *purchase* returns are exported; a customer files our sales returns
themselves. The layout follows "Sample Retur Faktur PM Template v.1.1": the faktur number and seller NPWP,
the return date (DD-MM-YYYY), DPP, DPP nilai lain, PPN and PPnBM, then one row per line of
the returned faktur. Rows not returned are included with quantity 0, because Coretax
refuses rows that differ from the original faktur. Amounts are computed per row with
commercial rounding to 2 decimals, as the template asks.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from xml.etree import ElementTree as ET

from django.db import models
from django.utils.translation import gettext as _

from apps.contacts.models import VatCollector
from apps.contacts.validators import npwp16
from apps.core.exceptions import ValidationFailed
from apps.core.money import ZERO
from apps.core.periods import month_end, month_start

from .models import Invoice, InvoiceKind, Item, ReturnNote, SpecialDocument

CENTS = Decimal("0.01")
HUNDRED = Decimal("100")
GOODS, SERVICES = "A", "B"
OTHER_UNIT = "UM.0033"  # "Lainnya"
# DJP unit codes (template sheet "REF"), keyed by the unit names people type.
UNIT_CODES = {
    "metrik ton": "UM.0001", "ton": "UM.0001", "wet ton": "UM.0002", "kg": "UM.0003", "kilogram": "UM.0003",
    "g": "UM.0004", "gr": "UM.0004", "gram": "UM.0004", "karat": "UM.0005", "kl": "UM.0006", "kiloliter": "UM.0006",
    "l": "UM.0007", "ltr": "UM.0007", "liter": "UM.0007", "barrel": "UM.0008", "mmbtu": "UM.0009", "ampere": "UM.0010",
    "cc": "UM.0011", "cm3": "UM.0011", "m2": "UM.0012", "meter persegi": "UM.0012", "m": "UM.0013", "meter": "UM.0013",
    "inch": "UM.0014", "inches": "UM.0014", "cm": "UM.0015", "sentimeter": "UM.0015", "yard": "UM.0016",
    "lusin": "UM.0017", "dozen": "UM.0017", "unit": "UM.0018", "set": "UM.0019", "lembar": "UM.0020", "sheet": "UM.0020",
    "pcs": "UM.0021", "pc": "UM.0021", "piece": "UM.0021", "buah": "UM.0021", "box": "UM.0022", "boks": "UM.0022",
    "dus": "UM.0022", "tahun": "UM.0023", "year": "UM.0023", "bulan": "UM.0024", "month": "UM.0024", "minggu": "UM.0025",
    "week": "UM.0025", "hari": "UM.0026", "day": "UM.0026", "jam": "UM.0027", "hour": "UM.0027", "menit": "UM.0028",
    "minute": "UM.0028", "persen": "UM.0029", "%": "UM.0029", "kegiatan": "UM.0030", "laporan": "UM.0031", "bahan": "UM.0032",
}


def unit_code(unit: str) -> str:
    return UNIT_CODES.get(unit.strip().lower(), OTHER_UNIT)


tin = npwp16  # the 16-digit NPWP Coretax expects


def _round(value: Decimal) -> Decimal:
    return value.quantize(CENTS, rounding=ROUND_HALF_UP)


def _rupiah(value: Decimal) -> str:
    return f"{Decimal(value).quantize(Decimal('1'), rounding=ROUND_HALF_UP):f}"


def _number(value: Decimal) -> str:
    value = _round(Decimal(value))
    return f"{value:.0f}" if value == value.to_integral() else f"{value:.2f}"


def _quantity(value: Decimal) -> str:
    return f"{Decimal(value).normalize():f}"


@dataclass(frozen=True)
class Row:
    kind: str
    name: str
    quantity: Decimal
    unit: str
    unit_price: Decimal
    returned: Decimal
    discount: Decimal
    tax_base: Decimal
    other_tax_base: Decimal
    uses_other_base: bool
    vat: Decimal
    luxury_rate: Decimal
    luxury: Decimal


def _rows(note: ReturnNote) -> list[Row]:
    returned = {line.invoice_line_id: line.quantity for line in note.lines.all()}
    rows = []
    for line in note.invoice.lines.select_related("item").order_by("id"):
        if line.tax_rate_percent is None:
            continue  # not on the faktur pajak
        quantity = returned.get(line.pk, Decimal("0"))
        gross = line.unit_price * quantity
        discount = _round(gross * line.discount_percent / HUNDRED)
        base = _round(gross - discount)
        uses_other = line.tax_dpp_numerator != line.tax_dpp_denominator
        other = _round(base * line.tax_dpp_numerator / line.tax_dpp_denominator) if uses_other else base
        rows.append(Row(
            kind=SERVICES if line.item_id and line.item.type == Item.Type.SERVICE else GOODS,
            name=line.description, quantity=line.quantity, unit=unit_code(line.item.unit if line.item_id else ""),
            unit_price=line.unit_price, returned=quantity, discount=discount, tax_base=base,
            other_tax_base=other, uses_other_base=uses_other, vat=_round(other * line.tax_rate_percent / HUNDRED),
            luxury_rate=line.tax_luxury_rate or ZERO, luxury=_round(other * (line.tax_luxury_rate or ZERO) / HUNDRED),
        ))
    return rows


def exportable_problem(note: ReturnNote) -> str | None:
    """Why this return can't go to Coretax, or None."""
    invoice, organization = note.invoice, note.organization
    if note.kind != InvoiceKind.PURCHASE:
        return _("Your customer records a sales return in Coretax; you approve it under Retur Pajak Keluaran.")
    if note.status != ReturnNote.Status.POSTED:
        return _("A voided return is not reported.")
    if not organization.is_pkp:
        return _("Only a VAT-registered business (PKP) reports returns of input VAT.")
    if len(tin(organization.npwp)) != 16:
        return _("Enter the organization's NPWP in the settings.")
    if invoice.special_document == SpecialDocument.EQUIVALENT:
        return _("A return of a bill equivalent to a Faktur Pajak goes in the Retur Dokumen Lain Masukan file.")
    if len(tin(invoice.party_npwp)) != 16:
        return _("The supplier's NPWP was not on the bill when it was issued.")
    if len("".join(ch for ch in invoice.tax_invoice_number if ch.isdigit())) not in (16, 17):
        return _("The bill has no Faktur Pajak number from the supplier.")
    if not note.tax_amount:
        return _("The returned lines carry no PPN.")
    return None


def _text(parent, tag: str, value) -> None:
    ET.SubElement(parent, tag).text = value


def build_xml(organization, notes: Iterable[ReturnNote]) -> bytes:
    """One Coretax import file for the given purchase returns (all must be exportable)."""
    root = ET.Element("InputTaxInvoiceReturn")
    _text(root, "TIN", tin(organization.npwp))
    data_list = ET.SubElement(root, "InputReturnDataList")
    for note in notes:
        problem = exportable_problem(note)
        if problem:
            raise ValidationFailed(f"{note.number}: {problem}")
        rows = _rows(note)
        totals = {name: sum((getattr(r, name) for r in rows), ZERO) for name in ("tax_base", "other_tax_base", "vat", "luxury")}
        data = ET.SubElement(data_list, "InputReturnData")
        document = ET.SubElement(data, "TransactionDocumentData")
        _text(document, "InvoiceNumber", "".join(ch for ch in note.invoice.tax_invoice_number if ch.isdigit()))
        _text(document, "SellerTIN", tin(note.invoice.party_npwp))
        _text(document, "ReturnDate", f"{note.date:%d-%m-%Y}")
        _text(document, "ReturnTaxBase", _number(totals["tax_base"]))
        _text(document, "ReturnOtherTaxBase", _number(totals["other_tax_base"]))
        _text(document, "ReturnVAT", _number(totals["vat"]))
        _text(document, "ReturnSTLG", _number(totals["luxury"]))
        details = ET.SubElement(data, "TransactionDetailsData")
        for row in rows:
            element = ET.SubElement(details, "Rows")
            for tag, value in [
                ("Type", row.kind), ("Name", row.name), ("Code", "000000"), ("Quantity", _quantity(row.quantity)),
                ("Unit", row.unit), ("UnitPrice", _number(row.unit_price)), ("STLGRate", _quantity(row.luxury_rate)),
                ("ReturnQuantity", _quantity(row.returned)), ("ReturnDiscount", _number(row.discount)),
                ("ReturnTaxBase", _number(row.tax_base)), ("ReturnOtherTaxBase", _number(row.other_tax_base)),
                ("ReturnOtherTaxBaseCheck", "true" if row.uses_other_base else "false"),
                ("ReturnVAT", _number(row.vat)), ("ReturnSTLG", _number(row.luxury)),
            ]:
                _text(element, tag, value)
        footer = ET.SubElement(details, "FooterRow")
        _text(footer, "ReturnTaxBaseTotal", _number(totals["tax_base"]))
        _text(footer, "ReturnOtherTaxBaseTotal", _number(totals["other_tax_base"]))
        _text(footer, "ReturnVATTotal", _number(totals["vat"]))
        _text(footer, "ReturnSTLGTotal", _number(totals["luxury"]))
    ET.indent(root, space="\t")
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="utf-8")


def filename(organization, label: str, kind: str = "retur-pajak-masukan") -> str:
    return f"{kind}-{tin(organization.npwp)}-{label}.xml"


def month_returns(organization, year: int, month: int):
    """Posted purchase returns of a month, oldest first (a month's import file)."""
    start, end = month_start(year, month), month_end(year, month)
    return (
        ReturnNote.objects.for_organization(organization)
        .filter(kind=InvoiceKind.PURCHASE, status=ReturnNote.Status.POSTED, date__gte=start, date__lte=end)
        .select_related("invoice", "organization").prefetch_related("lines").order_by("date", "id")
    )


# --- Faktur Pajak Keluaran (sales invoices) ----------------------------------------

# Template sheet REF-General; PER-11/PJ/2025 for what each code covers.
STANDARD, GOVERNMENT_COLLECTOR, OTHER_COLLECTOR, OTHER_TAX_BASE = "01", "02", "03", "04"
SPECIFIC_AMOUNT, OTHER_RATE = "05", "10"
COLLECTOR_CODES = {VatCollector.GOVERNMENT: GOVERNMENT_COLLECTOR, VatCollector.OTHER: OTHER_COLLECTOR}
STANDARD_RATE = Decimal("12")  # Art. 7(1) UU PPN since 2025; another rate takes code 10


NO_TIN = "0000000000000000"  # BuyerTin when the buyer is identified by another document
NO_NITKU = "000000"  # BuyerIDTKU then
TIN_DOCUMENT = "TIN"


def _line_kind(line) -> tuple[bool, bool, bool]:
    """What decides a line's code: PPN besaran tertentu, DPP nilai lain, a rate other than 12%."""
    specific = bool(line.tax_specific_amount)
    return specific, line.tax_dpp_numerator != line.tax_dpp_denominator, not specific and line.tax_rate_percent != STANDARD_RATE


def transaction_code(invoice: Invoice, lines) -> str:
    """In DJP's order of priority: 07/08 under a PPN facility; 02/03 for a buyer that collects
    the PPN (whatever the tax base, even besaran tertentu; DPP nilai lain stays in its own
    column); 06 for a tourist's VAT refund; 05 for PPN besaran tertentu; 09 for an asset not
    originally for sale; 10 for a rate other than 12%; else 04 for DPP nilai lain or 01."""
    specific, uses_other_base, other_rate = _line_kind(lines[0])
    if invoice.vat_facility:
        return invoice.vat_facility
    if invoice.buyer_vat_collector:
        return COLLECTOR_CODES[invoice.buyer_vat_collector]
    if invoice.delivery_kind == Invoice.DeliveryKind.TOURIST:
        return invoice.delivery_kind
    if specific:
        return SPECIFIC_AMOUNT
    if invoice.delivery_kind == Invoice.DeliveryKind.ASSET:
        return invoice.delivery_kind
    if other_rate:
        return OTHER_RATE
    return OTHER_TAX_BASE if uses_other_base else STANDARD


def buyer_identity(invoice: Invoice) -> dict[str, str] | None:
    """BuyerTin, BuyerDocument, BuyerCountry, BuyerDocumentNumber and BuyerIDTKU, or None
    when the buyer had neither an NPWP nor an identity document when the invoice was issued."""
    if invoice.party_tin:
        return {
            "BuyerTin": invoice.party_tin, "BuyerDocument": TIN_DOCUMENT, "BuyerCountry": invoice.party_country,
            "BuyerDocumentNumber": "-", "BuyerIDTKU": invoice.party_nitku,
        }
    if invoice.party_id_type and invoice.party_id_number:
        return {
            "BuyerTin": NO_TIN, "BuyerDocument": invoice.party_id_type, "BuyerCountry": invoice.party_country,
            "BuyerDocumentNumber": invoice.party_id_number, "BuyerIDTKU": NO_NITKU,
        }
    return None


def _taxed_lines(invoice: Invoice):
    return [line for line in invoice.lines.select_related("item").order_by("id") if line.tax_rate_percent is not None]


def is_aggregated(invoice: Invoice) -> bool:
    """A PKP retailer's sale to an end consumer without an NPWP or identity document: reported
    in the month's total (Faktur Pajak digunggung) in the SPT Masa PPN instead of uploaded."""
    return invoice.is_sales and invoice.organization.is_retailer and not invoice.is_export and buyer_identity(invoice) is None


def invoice_problem(invoice: Invoice) -> str | None:
    """Why this invoice can't go to Coretax as a Faktur Pajak Keluaran, or None."""
    organization = invoice.organization
    if invoice.kind != InvoiceKind.SALES:
        return _("Only sales invoices become a Faktur Pajak Keluaran.")
    if invoice.status not in Invoice.ISSUED_STATUSES:
        return _("Only issued invoices are reported.")
    if not organization.is_pkp:
        return _("Only a VAT-registered business (PKP) issues a Faktur Pajak.")
    if len(tin(organization.npwp)) != 16:
        return _("Enter the organization's NPWP in the settings.")
    if invoice.is_export:
        return _("An export is reported with its PEB in the Dokumen Lain Keluaran file, not as a faktur.")
    if is_aggregated(invoice):
        return _("A retail sale to a customer without an NPWP or identity document: it is reported in the month's total (digunggung) in the SPT Masa PPN, not as its own faktur.")
    if buyer_identity(invoice) is None:
        return _("The customer had neither an NPWP nor an identity document (NIK, passport) when the invoice was issued. Enter one on the contact for future invoices, or create this faktur in Coretax directly.")
    lines = _taxed_lines(invoice)
    if not lines:
        return _("The invoice carries no PPN.")
    if len({line.tax_dpp_numerator != line.tax_dpp_denominator for line in lines}) > 1:
        return _("The invoice mixes the 11/12 tax base with the full tax base; Coretax needs a separate faktur for each.")
    if len({_line_kind(line) for line in lines}) > 1:
        return _("The invoice mixes PPN besaran tertentu or a rate other than 12% with other PPN; Coretax needs a separate faktur for each.")
    return None


@dataclass(frozen=True)
class LineAmounts:
    discount: Decimal
    base: Decimal  # harga jual / DPP
    other: Decimal  # DPP nilai lain
    vat: Decimal
    luxury: Decimal


def _line_base(line, quantity: Decimal | None = None) -> Decimal:
    gross = line.unit_price * (line.quantity if quantity is None else quantity)
    return _round(gross - _round(gross * line.discount_percent / HUNDRED))


def line_amounts(line, quantity: Decimal | None = None) -> LineAmounts:
    """A taxed line's figures as Coretax shows them (for ``quantity`` of it, e.g. returned)."""
    gross = line.unit_price * (line.quantity if quantity is None else quantity)
    discount = _round(gross * line.discount_percent / HUNDRED)
    base = _round(gross - discount)
    other = _round(base * line.tax_dpp_numerator / line.tax_dpp_denominator)
    luxury_rate = line.tax_luxury_rate or ZERO
    return LineAmounts(discount, base, other, _round(other * line.tax_rate_percent / HUNDRED), _round(other * luxury_rate / HUNDRED))


def _good_service(parent, line) -> None:
    amounts = line_amounts(line)
    discount, base, other = amounts.discount, amounts.base, amounts.other
    luxury_rate = line.tax_luxury_rate or ZERO
    # Besaran tertentu: the faktur shows the statutory rate, and the PPN is the fixed share of
    # the price (the template allows a PPN other than rate x base outside codes 01, 04 and 09).
    rate = STANDARD_RATE if line.tax_specific_amount else line.tax_rate_percent
    element = ET.SubElement(parent, "GoodService")
    for tag, value in [
        ("Opt", SERVICES if line.item_id and line.item.type == Item.Type.SERVICE else GOODS), ("Code", "000000"),
        ("Name", line.description), ("Unit", unit_code(line.item.unit if line.item_id else "")),
        ("Price", _number(line.unit_price)), ("Qty", _quantity(line.quantity)), ("TotalDiscount", _number(discount)),
        ("TaxBase", _number(base)), ("OtherTaxBase", _number(other)), ("VATRate", _quantity(rate)),
        ("VAT", _number(amounts.vat)), ("STLGRate", _quantity(luxury_rate)), ("STLG", _number(amounts.luxury)),
    ]:
        _text(element, tag, value)


def build_invoices_xml(organization, invoices: Iterable[Invoice]) -> bytes:
    """One Coretax "Faktur Pajak Keluaran" import file for the given sales invoices."""
    # As in DJP's sample: the schema reference on the root element.
    root = ET.Element("TaxInvoiceBulk", {
        "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance", "xsi:noNamespaceSchemaLocation": "TaxInvoice.xsd",
    })
    _text(root, "TIN", tin(organization.npwp))
    listing = ET.SubElement(root, "ListOfTaxInvoice")
    for invoice in invoices:
        problem = invoice_problem(invoice)
        if problem:
            raise ValidationFailed(f"{invoice.number}: {problem}")
        lines = _taxed_lines(invoice)
        buyer = buyer_identity(invoice)
        document_date = invoice.supporting_document_date
        element = ET.SubElement(listing, "TaxInvoice")
        for tag, value in [
            ("TaxInvoiceDate", f"{invoice.date:%Y-%m-%d}"), ("TaxInvoiceOpt", "Normal"),
            ("TrxCode", transaction_code(invoice, lines)), ("AddInfo", invoice.facility_info),
            ("CustomDoc", invoice.supporting_document), ("CustomDocMonthYear", f"{document_date:%m%Y}" if document_date else ""),
            ("RefDesc", invoice.number), ("FacilityStamp", invoice.facility_stamp),
            ("SellerIDTKU", invoice.seller_tin_nitku), ("BuyerTin", buyer["BuyerTin"]), ("BuyerDocument", buyer["BuyerDocument"]),
            ("BuyerCountry", buyer["BuyerCountry"]), ("BuyerDocumentNumber", buyer["BuyerDocumentNumber"]),
            ("BuyerName", invoice.party_name),
            ("BuyerAdress", ", ".join(part for part in (invoice.party_address, invoice.party_city) if part) or "-"),
            ("BuyerEmail", invoice.contact.email), ("BuyerIDTKU", buyer["BuyerIDTKU"]),
        ]:
            _text(element, tag, value)
        goods = ET.SubElement(element, "ListOfGoodService")
        for line in lines:
            _good_service(goods, line)
    ET.indent(root, space="\t")
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="utf-8")


def month_invoices(organization, year: int, month: int):
    """Issued sales invoices of a month, oldest first."""
    start, end = month_start(year, month), month_end(year, month)
    return (
        Invoice.objects.for_organization(organization)
        .filter(kind=InvoiceKind.SALES, status__in=Invoice.ISSUED_STATUSES,
                date__gte=start, date__lte=end)
        .select_related("organization", "contact").order_by("date", "id")
    )


# SPT Masa PPN, retail sales reported as a total (digunggung): DJP's "Retail_XML Induk IA5 IA9
# dan IB" converter v1.2 and its "SPT PPN Digunggung" sample. Normal fills 1.A5, 07/08 1.A9 and
# NoVAT (deliveries without PPN) 1.B; buyers stay anonymous (NIK of zeros, as in the sample).
RETAIL_NORMAL, RETAIL_NO_VAT = "Normal", "NoVAT"
ANONYMOUS_NIK = "0" * 16


@dataclass(frozen=True)
class RetailRow:
    serial: int  # numeric (xs:long): the invoice's or the return's id
    date: date
    buyer: str
    info: str  # the document number
    trx_code: str
    goods_or_services: str
    base: Decimal
    other: Decimal
    vat: Decimal
    luxury: Decimal


def _retail_group(invoice: Invoice, line) -> tuple[str, str]:
    code = (invoice.vat_facility or RETAIL_NORMAL) if line.tax_rate_percent is not None else RETAIL_NO_VAT
    return code, SERVICES if line.item_id and line.item.type == Item.Type.SERVICE else GOODS


def _retail_rows(serial, on, buyer, info, invoice, quantities, sign) -> list[RetailRow]:
    """One row per kind of delivery of ``quantities`` ({line: quantity}) of the invoice's lines."""
    groups: dict[tuple[str, str], list[Decimal]] = {}
    for line, quantity in quantities:
        key = _retail_group(invoice, line)
        if key[0] == RETAIL_NO_VAT:
            base = _line_base(line, quantity)
            amounts = [base, base, ZERO, ZERO]
        else:
            a = line_amounts(line, quantity)
            amounts = [a.base, a.other, a.vat, a.luxury]
        totals = groups.setdefault(key, [ZERO] * 4)
        for i, value in enumerate(amounts):
            totals[i] += value
    return [
        RetailRow(serial, on, buyer, info, code, kind, *(sign * value for value in totals))
        for (code, kind), totals in groups.items()
    ]


def month_retail(organization, year: int, month: int) -> list[RetailRow]:
    """A retailer's sales of the month to anonymous end consumers, one row per invoice and kind
    of delivery (taxed, facility or without PPN; goods or services). Returns of such sales
    recorded in the month subtract their share as negative rows (the PPN of a return reduces
    the period of the return; DJP's template has no return field of its own)."""
    rows = []
    invoices = Invoice.objects.for_organization(organization).filter(
        kind=InvoiceKind.SALES, date__year=year, date__month=month,
        status__in=Invoice.ISSUED_STATUSES,
    ).order_by("date", "id")
    for invoice in invoices:
        if is_aggregated(invoice):
            lines = invoice.lines.select_related("item").order_by("id")
            rows += _retail_rows(invoice.pk, invoice.date, invoice.party_name, invoice.number, invoice,
                                 [(line, line.quantity) for line in lines], 1)
    notes = ReturnNote.objects.for_organization(organization).filter(
        kind=InvoiceKind.SALES, date__year=year, date__month=month, status=ReturnNote.Status.POSTED,
    ).select_related("invoice", "invoice__organization").order_by("date", "id")
    for note in notes:
        if is_aggregated(note.invoice):
            returned = [(r.invoice_line, r.quantity) for r in note.lines.select_related("invoice_line__item").order_by("id")]
            rows += _retail_rows(note.pk, note.date, note.invoice.party_name, f"Retur {note.number} atas {note.invoice.number}",
                                 note.invoice, returned, -1)
    return rows


def build_retail_xml(organization, year: int, month: int, rows: list[RetailRow]) -> bytes:
    root = ET.Element("RetailInvoiceBulk", {
        "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance", "xsi:noNamespaceSchemaLocation": "schema.xsd",
    })
    _text(root, "TIN", tin(organization.npwp))
    _text(root, "TaxPeriodMonth", str(month))
    _text(root, "TaxPeriodYear", str(year))
    listing = ET.SubElement(root, "ListOfRetailInvoice")
    for row in rows:
        element = ET.SubElement(listing, "RetailInvoice")
        for tag, value in [
            ("TrxCode", row.trx_code), ("BuyerName", row.buyer or "Konsumen akhir"), ("BuyerIdOpt", "NIK"),
            ("BuyerIdNumber", ANONYMOUS_NIK), ("GoodServiceOpt", row.goods_or_services),
            ("SerialNo", str(row.serial)),  # numeric (xs:long): the document number goes in Info
            # Whole rupiah (xs:int; DJP's converter rounds them).
            ("TransactionDate", row.date.isoformat()), ("TaxBaseSellingPrice", _rupiah(row.base)),
            ("OtherTaxBaseSellingPrice", _rupiah(row.other)), ("VAT", _rupiah(row.vat)), ("STLG", _rupiah(row.luxury)),
            ("Info", row.info),
        ]:
            _text(element, tag, value)
    ET.indent(root, space="\t")
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="utf-8")


# SPT Masa PPN "Dokumen Lain": deliveries and acquisitions on a document other than a Faktur
# Pajak, in DJP's templates ("Create DOk Lain Keluaran/Masukan", "Retur DOk Lain Masukan" and
# their sample XML). Codes from the templates' reference sheets: TransactionType 02 export,
# 03 import, 04 purchase; TransactionDetail 11-13 exports of goods/intangibles/services, 14 import
# of goods; TransactionDocument 4/5 export notice of goods/of services, 6 import notice and
# payment (PIB), 8 special documents.
EXPORT_CODES = {  # special document: (TransactionDetail, TransactionDocument)
    SpecialDocument.EXPORT_GOODS: ("11", "4"), SpecialDocument.EXPORT_INTANGIBLE: ("12", "5"),
    SpecialDocument.EXPORT_SERVICES: ("13", "5"),
}


def _issued(queryset):
    return queryset.filter(status__in=Invoice.ISSUED_STATUSES)


def month_exports(organization, year: int, month: int) -> tuple[list[Invoice], list[str]]:
    """Exports reported in the month: by the date of their export declaration (PEB); those of
    the month without one yet are listed as problems."""
    exports = _issued(Invoice.objects.for_organization(organization).filter(kind=InvoiceKind.SALES, special_document__in=list(EXPORT_CODES)))
    rows = list(exports.filter(special_document_date__year=year, special_document_date__month=month).order_by("special_document_date", "id"))
    problems = [
        _("%(number)s: enter its document (PEB or the bill's own number) first.") % {"number": i.number}
        for i in exports.filter(special_document_number="", date__year=year, date__month=month)
    ]
    return rows, problems


def build_exports_xml(organization, invoices) -> bytes:
    root = ET.Element("SpecialDocBulk", {
        "xsi:noNamespaceSchemaLocation": "schema.xsd", "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
    })
    _text(root, "TIN", tin(organization.npwp))
    listing = ET.SubElement(root, "ListOfSpecialDoc")
    for invoice in invoices:
        detail, document = EXPORT_CODES[invoice.special_document]
        element = ET.SubElement(listing, "SpecialDoc")
        for tag, value in [
            ("TransactionType", "02"), ("TransactionDetail", detail), ("TransactionDocument", document),
            ("AdditionalInformation", None),  # only for codes 07/08
            ("DocumentNumber", invoice.special_document_number), ("DocumentDate", invoice.special_document_date.isoformat()),
            ("Reference", None), ("BuyerTIN", NO_TIN), ("BuyerName", invoice.party_name),
            ("BuyerAddress", invoice.contact_address or invoice.contact.address or "-"), ("TaxBase", _rupiah(invoice.subtotal)),
            ("VAT", "0"), ("STLG", "0"),  # exports bear PPN at 0%
        ]:
            _text(element, tag, value)
    ET.indent(root, space="\t")
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="utf-8")


@dataclass(frozen=True)
class SpecialInput:
    number: str
    date: date
    trx_type: str
    trx_code: str
    trx_document: str
    base: Decimal
    vat: Decimal
    luxury: Decimal
    seller_tin: str
    seller_name: str


def _taxed_base(invoice: Invoice, quantities=None) -> tuple[Decimal, Decimal, Decimal]:
    """(DPP, PPN, PPnBM) of the invoice's taxed lines (or of ``quantities`` {line id: qty})."""
    base = vat = luxury = ZERO
    for line in invoice.lines.order_by("id"):
        if line.tax_rate_percent is None or (quantities is not None and line.pk not in quantities):
            continue
        a = line_amounts(line, None if quantities is None else quantities[line.pk])
        base, vat, luxury = base + a.other, vat + a.vat, luxury + a.luxury
    return base, vat, luxury


def month_special_inputs(organization, year: int, month: int) -> tuple[list[SpecialInput], list[str]]:
    """The month's Dokumen Lain Masukan: PIBs (PPN impor) and bills equivalent to a faktur
    (by their document's date), with what keeps others out."""
    from apps.taxes.models import ImportDeclaration  # taxes reads invoicing too

    rows, problems = [], []
    for pib in ImportDeclaration.objects.for_organization(organization).filter(
        date__year=year, date__month=month, status=ImportDeclaration.Status.POSTED,
    ).order_by("date", "id"):
        rows.append(SpecialInput(pib.number, pib.date, "03", "14", "6", pib.customs_value, pib.vat, pib.luxury_tax, NO_TIN, pib.supplier_name))
    bills = _issued(Invoice.objects.for_organization(organization).filter(kind=InvoiceKind.PURCHASE, special_document=SpecialDocument.EQUIVALENT))
    for bill in bills.filter(special_document_date__year=year, special_document_date__month=month).order_by("special_document_date", "id"):
        base, vat, luxury = _taxed_base(bill)
        rows.append(SpecialInput(bill.special_document_number, bill.special_document_date, "04", "01", "8", base, vat, luxury,
                                 tin(bill.party_npwp) if len(tin(bill.party_npwp)) == 16 else NO_TIN, bill.party_name))
    for bill in bills.filter(special_document_number="", date__year=year, date__month=month):
        problems.append(_("%(number)s: enter its document (PEB or the bill's own number) first.") % {"number": bill.number})
    return rows, problems


def build_special_inputs_xml(organization, rows: list[SpecialInput]) -> bytes:
    root = ET.Element("InputSpecialDocBulk", {
        "xsi:noNamespaceSchemaLocation": "SpecialInput.xsd", "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
    })
    _text(root, "TIN", tin(organization.npwp))
    listing = ET.SubElement(root, "ListOfSpecialDoc")
    for row in rows:
        element = ET.SubElement(listing, "SpecialDoc")
        for tag, value in [
            ("SpecialDocNo", row.number), ("SpecialDocDate", row.date.isoformat()), ("TrxType", row.trx_type), ("TrxCode", row.trx_code),
            ("TrxDocument", row.trx_document), ("TaxPeriodMonth", str(row.date.month)), ("TaxPeriodYear", str(row.date.year)),
            ("TotalTaxBase", _rupiah(row.base)), ("TotalVAT", _rupiah(row.vat)), ("TotalSTLG", _rupiah(row.luxury)),
            ("SellerTin", row.seller_tin), ("SellerName", row.seller_name),
        ]:
            _text(element, tag, value)
    ET.indent(root, space="\t")
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="utf-8")


def month_special_returns(organization, year: int, month: int) -> list[ReturnNote]:
    """Returns of the month on bills equivalent to a faktur (Retur Dokumen Lain Masukan)."""
    return list(ReturnNote.objects.for_organization(organization).filter(
        kind=InvoiceKind.PURCHASE, date__year=year, date__month=month, status=ReturnNote.Status.POSTED,
        invoice__special_document=SpecialDocument.EQUIVALENT,
    ).exclude(invoice__special_document_number="").select_related("invoice").order_by("date", "id"))


def build_special_returns_xml(organization, notes) -> bytes:
    root = ET.Element("InputSpecialDocReturn")
    _text(root, "TIN", tin(organization.npwp))
    listing = ET.SubElement(root, "InputReturnDataList")
    for note in notes:
        bill = note.invoice
        base, vat, luxury = _taxed_base(bill, {line.invoice_line_id: line.quantity for line in note.lines.all()})
        element = ET.SubElement(listing, "InputReturnData")
        for tag, value in [
            ("DocumentNumber", bill.special_document_number), ("ReturnDocumentNumber", note.number), ("TransactionType", "04"),
            ("DocumentType", "01"), ("TransactionDetail", "01"), ("TransactionDocument", "08"), ("DocumentDate", note.date.isoformat()),
            ("Period", str(note.date.month)), ("Year", str(note.date.year)),
            ("SellerTIN", tin(bill.party_npwp) if len(tin(bill.party_npwp)) == 16 else NO_TIN), ("SellerTaxpayerName", bill.party_name),
            ("ReturnTaxBase", _rupiah(base)), ("ReturnVAT", _rupiah(vat)), ("ReturnSTLG", _rupiah(luxury)),
        ]:
            _text(element, tag, value)
    ET.indent(root, space="\t")
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="utf-8")


class SpecialFile(models.TextChoices):
    """The Dokumen Lain files of a month."""

    OUTPUT = "keluaran", "Dokumen Lain Keluaran"
    INPUT = "masukan", "Dokumen Lain Masukan"
    INPUT_RETURNS = "retur-masukan", "Retur Dokumen Lain Masukan"


@dataclass(frozen=True)
class SpecialMonth:
    month: int
    exports: int
    inputs: int
    returns: int
    problems: list[str]


def special_months(organization, year: int) -> list[SpecialMonth]:
    """The months of a year with Dokumen Lain to report (PKP only)."""
    if not organization.is_pkp:
        return []
    months = []
    for month in range(1, 13):
        exports, export_problems = month_exports(organization, year, month)
        inputs, input_problems = month_special_inputs(organization, year, month)
        returns = month_special_returns(organization, year, month)
        if exports or inputs or returns or export_problems or input_problems:
            months.append(SpecialMonth(month, len(exports), len(inputs), len(returns), export_problems + input_problems))
    return months


def special_file(organization, year: int, month: int, kind: str) -> bytes | None:
    """A month's Dokumen Lain file, or None when it has nothing to report."""
    if kind == SpecialFile.OUTPUT:
        rows, _problems = month_exports(organization, year, month)
        return build_exports_xml(organization, rows) if rows else None
    if kind == SpecialFile.INPUT:
        rows, _problems = month_special_inputs(organization, year, month)
        return build_special_inputs_xml(organization, rows) if rows else None
    notes = month_special_returns(organization, year, month)
    return build_special_returns_xml(organization, notes) if notes else None
