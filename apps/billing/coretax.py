"""The platform's own Faktur Pajak Keluaran: one per paid subscription invoice, in the same
Coretax import layout as the tenants' sales invoices (``apps.invoicing.coretax``), with the
seller being the platform (``BILLING_SELLER_*``). Code 04: a service with DPP nilai lain 11/12.
Buyers without an NPWP are left out (create those fakturs in Coretax directly)."""

from datetime import date
from xml.etree import ElementTree as ET

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.contacts.validators import HEAD_OFFICE, npwp16
from apps.invoicing.coretax import OTHER_TAX_BASE, SERVICES, _number, _text

from .domain import BillingPeriod
from .models import SubscriptionInvoice

UNIT = {BillingPeriod.MONTHLY: "UM.0024", BillingPeriod.YEARLY: "UM.0023"}  # Bulan, Tahun


def month_invoices(year: int, month: int):
    """Paid subscription invoices of a month (by payment date) that still need their faktur."""
    start = date(year, month, 1)
    end = date(year + (month == 12), month % 12 + 1, 1)
    invoices = SubscriptionInvoice.objects.filter(
        status=SubscriptionInvoice.Status.PAID, total__gt=0, tax_invoice_number="",
        paid_at__date__gte=start, paid_at__date__lt=end,
    ).select_related("plan").order_by("paid_at", "id")
    return [i for i in invoices if len(npwp16(i.buyer_npwp)) == 16]


def build_xml(invoices) -> bytes:
    seller = npwp16(settings.BILLING_SELLER_NPWP)
    root = ET.Element("TaxInvoiceBulk", {
        "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance", "xsi:noNamespaceSchemaLocation": "TaxInvoice.xsd",
    })
    _text(root, "TIN", seller)
    listing = ET.SubElement(root, "ListOfTaxInvoice")
    for invoice in invoices:
        buyer = npwp16(invoice.buyer_npwp)
        element = ET.SubElement(listing, "TaxInvoice")
        for tag, value in [
            ("TaxInvoiceDate", f"{timezone.localdate(invoice.paid_at):%Y-%m-%d}"), ("TaxInvoiceOpt", "Normal"),
            ("TrxCode", OTHER_TAX_BASE), ("AddInfo", ""), ("CustomDoc", ""), ("CustomDocMonthYear", ""),
            ("RefDesc", invoice.number), ("FacilityStamp", ""), ("SellerIDTKU", seller + HEAD_OFFICE),
            ("BuyerTin", buyer), ("BuyerDocument", "TIN"), ("BuyerCountry", "IDN"), ("BuyerDocumentNumber", "-"),
            ("BuyerName", invoice.buyer_name), ("BuyerAdress", invoice.buyer_address or "-"), ("BuyerEmail", invoice.buyer_email),
            ("BuyerIDTKU", buyer + HEAD_OFFICE),
        ]:
            _text(element, tag, value)
        goods = ET.SubElement(element, "ListOfGoodService")
        good = ET.SubElement(goods, "GoodService")
        net = invoice.net_subtotal
        for tag, value in [
            ("Opt", SERVICES), ("Code", "000000"),
            ("Name", f"Langganan {settings.BILLING_SELLER_NAME} paket {invoice.plan.name}"),  # stored records stay Indonesian
            ("Unit", UNIT[BillingPeriod(invoice.billing_period)]), ("Price", _number(net)), ("Qty", "1"), ("TotalDiscount", "0"),
            ("TaxBase", _number(net)), ("OtherTaxBase", _number(invoice.tax_base)), ("VATRate", "12"),
            ("VAT", _number(invoice.tax_amount)), ("STLGRate", "0"), ("STLG", "0"),
        ]:
            _text(good, tag, value)
    ET.indent(root, space="\t")
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="utf-8")


@transaction.atomic
def record_numbers(items) -> int:
    """Record the faktur numbers Coretax issued (``ItemResult``s), once per invoice."""
    recorded = 0
    for item in items:
        if not item.number:
            continue
        recorded += SubscriptionInvoice.objects.filter(
            number=item.reference, status=SubscriptionInvoice.Status.PAID, tax_invoice_number="",
        ).update(tax_invoice_number=item.number, updated_at=timezone.now())
    return recorded
