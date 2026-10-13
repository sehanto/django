"""Coretax XML for purchase returns (nota retur, "Retur Pajak Masukan").

Since 2025 the buyer creates the nota retur in Coretax DJP and the seller approves it, so
only *purchase* returns are exported; a customer files our sales returns themselves.

The layout follows DJP's "Sample Retur Faktur PM Template v.1.1" (converter v1.6,
pajak.go.id, Template XML dan Converter Excel ke XML): the faktur number and seller NPWP,
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

from django.utils.translation import gettext as _

from apps.core.exceptions import ValidationFailed
from apps.core.money import ZERO

from .models import InvoiceKind, Item, ReturnNote

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


def tin(npwp: str) -> str:
    """The 16-digit NPWP Coretax expects (a 15-digit NPWP is prefixed with 0)."""
    digits = "".join(ch for ch in npwp if ch.isdigit())
    return "0" + digits if len(digits) == 15 else digits


def _round(value: Decimal) -> Decimal:
    return value.quantize(CENTS, rounding=ROUND_HALF_UP)


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
        totals = {name: sum((getattr(r, name) for r in rows), ZERO) for name in ("tax_base", "other_tax_base", "vat")}
        data = ET.SubElement(data_list, "InputReturnData")
        document = ET.SubElement(data, "TransactionDocumentData")
        _text(document, "InvoiceNumber", "".join(ch for ch in note.invoice.tax_invoice_number if ch.isdigit()))
        _text(document, "SellerTIN", tin(note.invoice.party_npwp))
        _text(document, "ReturnDate", f"{note.date:%d-%m-%Y}")
        _text(document, "ReturnTaxBase", _number(totals["tax_base"]))
        _text(document, "ReturnOtherTaxBase", _number(totals["other_tax_base"]))
        _text(document, "ReturnVAT", _number(totals["vat"]))
        _text(document, "ReturnSTLG", "0")
        details = ET.SubElement(data, "TransactionDetailsData")
        for row in rows:
            element = ET.SubElement(details, "Rows")
            for tag, value in [
                ("Type", row.kind), ("Name", row.name), ("Code", "000000"), ("Quantity", _quantity(row.quantity)),
                ("Unit", row.unit), ("UnitPrice", _number(row.unit_price)), ("STLGRate", "0"),
                ("ReturnQuantity", _quantity(row.returned)), ("ReturnDiscount", _number(row.discount)),
                ("ReturnTaxBase", _number(row.tax_base)), ("ReturnOtherTaxBase", _number(row.other_tax_base)),
                ("ReturnOtherTaxBaseCheck", "true" if row.uses_other_base else "false"),
                ("ReturnVAT", _number(row.vat)), ("ReturnSTLG", "0"),
            ]:
                _text(element, tag, value)
        footer = ET.SubElement(details, "FooterRow")
        _text(footer, "ReturnTaxBaseTotal", _number(totals["tax_base"]))
        _text(footer, "ReturnOtherTaxBaseTotal", _number(totals["other_tax_base"]))
        _text(footer, "ReturnVATTotal", _number(totals["vat"]))
        _text(footer, "ReturnSTLGTotal", "0")
    ET.indent(root, space="\t")
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="utf-8")


def filename(organization, label: str) -> str:
    return f"retur-pajak-masukan-{tin(organization.npwp)}-{label}.xml"


def month_returns(organization, year: int, month: int):
    """Posted purchase returns of a month, oldest first (a month's import file)."""
    start = date(year, month, 1)
    end = date(year + (month == 12), month % 12 + 1, 1)
    return (
        ReturnNote.objects.for_organization(organization)
        .filter(kind=InvoiceKind.PURCHASE, status=ReturnNote.Status.POSTED, date__gte=start, date__lt=end)
        .select_related("invoice", "organization").prefetch_related("lines").order_by("date", "id")
    )
