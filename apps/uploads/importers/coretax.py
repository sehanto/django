"""What Coretax gives back, uploaded from its download (XLSX or CSV) instead of the API connection:

* the Faktur Pajak numbers of the sales invoices sent to it (matched by the reference this
  project puts on each faktur, the invoice number, or else by buyer, date and PPN);
* the input VAT fakturs of the purchases (Pajak Masukan), matched to the bills by the seller's
  NPWP and the PPN: the bill takes the faktur's number, and the file shows the fakturs without a
  bill (input VAT not yet recorded) and the bills of its months still without a faktur (input VAT
  that can't be credited without one, UU PPN Art. 9(9));
* the bukti potong numbers of the income tax withheld from suppliers and employees (matched by
  the document number on the bukti potong and the recipient's NPWP/NIK, payslips by recipient and
  tax period).

A row that matches nothing, or a document that already has another number, is skipped with the
reason: the file is a report from elsewhere, so it is applied as far as it agrees with the books.
Numbers are only ever recorded once (the services and the database refuse a second one).
"""

import re
from datetime import timedelta

from django.db.models import Q
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from apps.contacts.validators import npwp16
from apps.invoicing import services as invoicing_services
from apps.invoicing.models import Invoice, InvoiceKind, Payment
from apps.payroll import services as payroll_services
from apps.payroll.models import PayrollRun, Payslip
from apps.taxes import services as tax_services
from apps.taxes.models import SelfDeposit

from ..domain import MONTHS, Column, RowOutcome, digits, normalized_header
from .base import Importer, RowError

# Coretax statuses of a faktur or bukti potong that is no longer (or not yet) valid.
NOT_VALID = {normalized_header(s) for s in (
    "canceled", "cancelled", "batal", "dibatalkan", "rejected", "ditolak", "deleted", "dihapus", "draft", "created",
    "amended", "diganti", "invalid", "tidak valid", "pembatalan",
)}


def _tin(text: str) -> str:
    """An NPWP as Coretax writes it: 16 digits (a 15-digit NPWP with a leading 0)."""
    number = digits(text)
    return npwp16(number) if len(number) == 15 else number


def _same_tin(a: str, b: str) -> bool:
    return bool(a) and bool(b) and _tin(a) == _tin(b)


def _tin_matches(text: str, candidates) -> bool:
    """Whether an NPWP/NIK (or a foreign TIN or passport) is one of the recipient's."""
    return any(candidate and (_same_tin(text, candidate) or digits(text) == digits(candidate)) for candidate in candidates)


def _faktur_number(text: str) -> str:
    number = digits(text)
    if len(number) not in (16, 17):
        raise RowError(_("%(value)s is not a Faktur Pajak number (17 digits).") % {"value": text})
    return number


def _invalid(row) -> str:
    status = row.get("status")
    if status and normalized_header(status) in NOT_VALID:
        return _("Status %(status)s in Coretax: not recorded.") % {"status": status}
    return ""


class OutputFakturImporter(Importer):
    kind = "output_fakturs"
    group = "coretax"
    title = gettext_lazy("Faktur Pajak numbers (sales)")
    description = gettext_lazy(
        "Coretax's list of output fakturs (Pajak Keluaran), downloaded after the faktur file was uploaded and approved: "
        "each sales invoice takes its Faktur Pajak number. The faktur's reference is the invoice number; without it, "
        "the buyer's NPWP, the date and the PPN find the invoice."
    )
    columns = (
        Column("number", "faktur number", ("nomor faktur pajak", "nomor faktur", "no faktur", "nsfp", "tax invoice number"), required=True),
        Column("reference", "reference", ("referensi", "refdesc", "nomor referensi", "invoice number", "nomor invoice")),
        Column("buyer_tin", "buyer NPWP", ("npwp pembeli", "npwp/nik pembeli", "npwp pembeli/identitas lainnya", "buyer tin", "id pembeli")),
        Column("date", "faktur date", ("tanggal faktur pajak", "tanggal faktur", "tax invoice date")),
        Column("vat", "PPN", ("ppn", "vat", "jumlah ppn")),
        Column("status", "status", ("status faktur", "status approval")),
    )
    example = (
        {"number": "04002600000012345", "reference": "INV/2026/01/0001", "buyer_tin": "0012345678901000", "date": "15/01/2026",
         "vat": "1100000", "status": "APPROVED"},
    )

    def row_label(self, row):
        return row.get("reference") or row.get("number")

    def _invoice(self, context, row) -> Invoice | None:
        sales = Invoice.objects.for_organization(context.organization).filter(
            kind=InvoiceKind.SALES, status__in=Invoice.ISSUED_STATUSES, opening_balance=False,
        )
        reference = row.get("reference")
        if reference:
            invoice = sales.filter(number=reference).first()
            if invoice is not None:
                return invoice
        on, vat = self.date(row, "date", _("faktur date")), self.amount(row, "vat", _("PPN"))
        if on is None or vat is None or not row.get("buyer_tin"):
            return None
        candidates = [
            i for i in sales.filter(date=on, tax_invoice_number="").filter(Q(tax_amount=vat) | Q(vat_not_collected=vat))
            if _same_tin(i.contact_npwp, row.get("buyer_tin")) or digits(row.get("buyer_tin")) == i.contact_id_number
        ]
        return candidates[0] if len(candidates) == 1 else None

    def apply_row(self, context, row):
        number = _faktur_number(row.get("number"))
        if reason := _invalid(row):
            return RowOutcome.SKIPPED, number, reason
        invoice = self._invoice(context, row)
        if invoice is None:
            return RowOutcome.SKIPPED, number, _("No sales invoice found for this faktur (it may have been made in Coretax directly).")
        if invoice.tax_invoice_number == number:
            return RowOutcome.SKIPPED, invoice.number, _("Already recorded.")
        if invoice.tax_invoice_number:
            return RowOutcome.SKIPPED, invoice.number, _("The invoice already has Faktur Pajak %(number)s: check which one is valid.") % {
                "number": invoice.tax_invoice_number}
        vat = self.amount(row, "vat", _("PPN"))
        if vat is not None and vat not in (invoice.tax_amount, invoice.vat_not_collected):
            return RowOutcome.SKIPPED, invoice.number, _("The faktur's PPN %(faktur)s differs from the invoice's %(invoice)s: not recorded.") % {
                "faktur": f"{vat:,.0f}", "invoice": f"{invoice.tax_amount or invoice.vat_not_collected:,.0f}"}
        invoicing_services.record_tax_invoice_number(invoice=invoice, number=number)
        return RowOutcome.UPDATED, invoice.number, number


class InputFakturImporter(Importer):
    kind = "input_fakturs"
    group = "coretax"
    title = gettext_lazy("Input VAT fakturs (purchases)")
    description = gettext_lazy(
        "Coretax's list of input fakturs (Pajak Masukan): each matching purchase bill (same supplier NPWP and PPN, dated "
        "within a month of the faktur) takes its Faktur Pajak number. The check lists the fakturs without a bill and the "
        "bills of those months still without a faktur."
    )
    columns = (
        Column("seller_tin", "seller NPWP", ("npwp penjual", "npwp/nik penjual", "seller tin", "npwp pkp penjual"), required=True),
        Column("seller_name", "seller name", ("nama penjual", "nama pkp penjual", "seller name")),
        Column("number", "faktur number", ("nomor faktur pajak", "nomor faktur", "no faktur", "nsfp", "tax invoice number"), required=True),
        Column("date", "faktur date", ("tanggal faktur pajak", "tanggal faktur", "tax invoice date"), required=True),
        Column("base", "tax base", ("dpp", "harga jual/penggantian/dpp", "harga jual", "tax base")),
        Column("vat", "PPN", ("ppn", "vat", "jumlah ppn"), required=True),
        Column("status", "status", ("status faktur", "status approval")),
    )
    example = (
        {"seller_tin": "0098765432109000", "seller_name": "CV Pemasok Bahan", "number": "04002600000054321", "date": "20/01/2026",
         "base": "10000000", "vat": "1100000", "status": "APPROVED"},
    )

    def row_label(self, row):
        return f"{row.get('seller_name') or row.get('seller_tin')} {row.get('number')}".strip()

    def prepare(self, context, table, report):
        context.months = set()

    def _bills(self, context):
        return Invoice.objects.for_organization(context.organization).filter(
            kind=InvoiceKind.PURCHASE, status__in=Invoice.ISSUED_STATUSES, opening_balance=False,
        )

    def apply_row(self, context, row):
        number = _faktur_number(row.get("number"))
        if reason := _invalid(row):
            return RowOutcome.SKIPPED, number, reason
        on = self.date(row, "date", _("faktur date"), required=True)
        vat = self.amount(row, "vat", _("PPN"), required=True)
        context.months.add((on.year, on.month))
        bills = self._bills(context)
        recorded = bills.filter(tax_invoice_number=number).first()
        if recorded is not None:
            return RowOutcome.SKIPPED, recorded.number, _("Already recorded.")
        candidates = [
            bill for bill in bills.filter(tax_invoice_number="", tax_amount=vat, date__gte=on - timedelta(days=31), date__lte=on + timedelta(days=31))
            .select_related("contact").order_by("date", "id")
            if _same_tin(bill.contact_npwp, row.get("seller_tin"))
        ]
        if not candidates:
            return RowOutcome.SKIPPED, number, _("No purchase bill with this supplier and PPN: record the bill to credit this input VAT.")
        bill = min(candidates, key=lambda b: (abs((b.date - on).days), b.pk))
        base = self.amount(row, "base", _("tax base"))
        if base is not None and base != bill.subtotal:
            note = _("Its tax base %(faktur)s differs from the bill's %(bill)s.") % {"faktur": f"{base:,.0f}", "bill": f"{bill.subtotal:,.0f}"}
        else:
            note = ""
        invoicing_services.record_tax_invoice_number(invoice=bill, number=number)
        return RowOutcome.UPDATED, f"{bill.number} · {bill.contact_name}", f"{number} {note}".strip()

    def finish(self, context, table, report):
        for year, month in sorted(context.months):
            for bill in self._bills(context).filter(date__year=year, date__month=month, tax_amount__gt=0, tax_invoice_number="",
                                                    special_document="").order_by("date", "id"):
                report.notes.append(_("Bill %(number)s from %(supplier)s (%(date)s) has no faktur in Coretax yet: its input VAT can't be credited without one.") % {
                    "number": bill.number, "supplier": bill.contact_name, "date": f"{bill.date:%d-%m-%Y}"})


def _period(row) -> tuple[int, int] | None:
    """The tax period from ``Masa Pajak``/``Tahun Pajak`` (``1``, ``01``, ``Januari``; or ``01-2026``, ``012026``)."""
    month_text, year_text = row.get("month").strip(), row.get("year").strip()
    if not month_text:
        return None
    combined = re.match(r"^(\d{1,2})\s*[-/ ]?\s*(\d{4})$", month_text)
    if combined:
        month, year = int(combined[1]), int(combined[2])
    else:
        month = int(month_text) if month_text.isdigit() else MONTHS.get(month_text.lower(), 0)
        year = int(year_text) if year_text.isdigit() else 0
    if not 1 <= month <= 12 or year < 2000:
        raise RowError(_("The tax period %(month)s %(year)s can't be read.") % {"month": month_text, "year": year_text})
    return year, month


class WithholdingSlipImporter(Importer):
    kind = "withholding_slips"
    group = "coretax"
    title = gettext_lazy("Bukti potong numbers")
    description = gettext_lazy(
        "Coretax's list of bukti potong (eBupot) issued for the files uploaded from here: the supplier payments, payslips "
        "and self-deposited PPh take their numbers. Payments and self deposits are found by the document number on the "
        "bukti potong (the bill's number) and the recipient's NPWP/NIK, payslips by the recipient and the tax period."
    )
    columns = (
        Column("number", "bukti potong number", ("nomor bukti potong", "nomor bupot", "no bukti potong", "nomor bukti pemotongan",
                                                  "nomor bukti pemotongan/pemungutan", "slip number"), required=True),
        Column("recipient_tin", "recipient NPWP/NIK", ("npwp/nik penerima penghasilan", "npwp penerima penghasilan", "npwp/nik penerima",
                                                        "npwp penerima", "nik penerima", "tin penerima", "id penerima penghasilan",
                                                        "npwp/nik", "npwp", "nik")),
        Column("document", "document number", ("nomor dokumen", "no dokumen", "nomor dokumen referensi", "dokumen referensi")),
        Column("tax_object", "tax object code", ("kode objek pajak", "kode objek")),
        Column("base", "gross income", ("penghasilan bruto", "dasar pengenaan pajak", "dpp", "jumlah penghasilan bruto")),
        Column("month", "tax period", ("masa pajak", "masa")),
        Column("year", "tax year", ("tahun pajak", "tahun")),
        Column("status", "status", ("status bukti potong", "status dokumen")),
    )
    example = (
        {"number": "2600123456", "recipient_tin": "0098765432109000", "document": "BILL/2026/01/0007", "tax_object": "24-104-01",
         "base": "5000000", "month": "1", "year": "2026", "status": "NORMAL"},
    )

    def row_label(self, row):
        return " ".join(part for part in (row.get("number"), row.get("document")) if part)

    def _payment(self, context, row) -> Payment | None:
        document, tin = row.get("document"), row.get("recipient_tin")
        if not document:
            return None
        payments = Payment.objects.for_organization(context.organization).filter(
            invoice__kind=InvoiceKind.PURCHASE, invoice__number=document, status=Payment.Status.POSTED,
        ).exclude(income_tax_kind="").select_related("invoice", "invoice__contact").order_by("date", "id")
        if row.get("tax_object"):
            payments = payments.filter(income_tax_object=row.get("tax_object"))
        base = self.amount(row, "base", _("gross income"))
        if base is not None:
            payments = payments.filter(income_tax_base=base)
        matches = [
            p for p in payments
            if not tin or _tin_matches(tin, (p.invoice.party_tin, p.invoice.party_id_number, p.invoice.contact.foreign_tin))
        ]
        open_ones = [p for p in matches if not p.withholding_slip]
        return (open_ones or matches or [None])[0]

    def _payslip(self, context, row) -> Payslip | None:
        tin, period = row.get("recipient_tin"), _period(row)
        if not tin or period is None:
            return None
        slips = Payslip.objects.filter(
            run__organization=context.organization, run__status=PayrollRun.Status.POSTED, run__year=period[0], run__month=period[1],
        ).select_related("employee").order_by("id")
        matches = [s for s in slips if _tin_matches(tin, (s.employee.tin, s.employee.foreign_tin, s.employee.passport_number))]
        open_ones = [s for s in matches if not s.withholding_slip]
        return (open_ones or matches or [None])[0]

    def _self_deposit(self, context, row) -> SelfDeposit | None:
        document = row.get("document")
        if not document:
            return None
        deposits = SelfDeposit.objects.for_organization(context.organization).filter(
            document_number=document, status=SelfDeposit.Status.POSTED,
        )
        if row.get("tax_object"):
            deposits = deposits.filter(tax_object=row.get("tax_object"))
        return deposits.order_by("slip_number", "id").first()

    def apply_row(self, context, row):
        number = row.get("number")
        if reason := _invalid(row):
            return RowOutcome.SKIPPED, number, reason
        for find, record, current, label in (
            (self._payment, lambda p: invoicing_services.record_withholding_slip(payment=p, number=number), "withholding_slip",
             lambda p: f"{p.number} · {p.invoice.contact_name}"),
            (self._self_deposit, lambda d: tax_services.record_self_deposit_slip(deposit=d, number=number), "slip_number",
             lambda d: d.document_number),
            (self._payslip, lambda s: payroll_services.record_slip_number(payslip=s, number=number), "withholding_slip",
             lambda s: f"{s.employee.name} {s.run.month:02d}/{s.run.year}"),
        ):
            target = find(context, row)
            if target is None:
                continue
            existing = getattr(target, current)
            if existing == number:
                return RowOutcome.SKIPPED, label(target), _("Already recorded.")
            if existing:
                return RowOutcome.SKIPPED, label(target), _("It already has bukti potong %(number)s.") % {"number": existing}
            record(target)
            return RowOutcome.UPDATED, label(target), number
        return RowOutcome.SKIPPED, number, _("No payment, payslip or self deposit found for this bukti potong.")

