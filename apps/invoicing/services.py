"""Invoice and payment use cases.

Lifecycle: draft → issued → partial → paid, or issued → void. Issuing posts
the journal; voiding reverses it. Documents are never hard-deleted after
issuance so the audit trail stays intact.
"""

from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.core.exceptions import InvalidStateTransition, PlanLimitExceeded, ValidationFailed
from apps.core.money import ZERO
from apps.core.sequences import next_document_number
from apps.ledger.domain import SystemRole
from apps.ledger.models import Account, JournalEntry
from apps.ledger.services import JournalLineInput, create_journal_entry, get_system_account, reverse_journal_entry

from .domain import InvoiceTotals, LineSpec, TaxSpec, allocate, compute_invoice_totals, payment_state
from .models import Invoice, InvoiceKind, InvoiceLine, Item, Payment
from .selectors import invoices_created_this_month

INVOICE_PREFIX = {InvoiceKind.SALES: "INV", InvoiceKind.PURCHASE: "BILL"}
PAYMENT_PREFIX = {InvoiceKind.SALES: "KM", InvoiceKind.PURCHASE: "KK"}
JOURNAL_SOURCE = {
    InvoiceKind.SALES: JournalEntry.Source.SALES_INVOICE,
    InvoiceKind.PURCHASE: JournalEntry.Source.PURCHASE_INVOICE,
}


@dataclass(frozen=True)
class InvoiceLineInput:
    description: str
    account: Account
    quantity: Decimal
    unit_price: Decimal
    discount_percent: Decimal = ZERO
    tax_rate: object = None  # taxes.TaxRate | None
    item: Item | None = None

    def to_spec(self) -> LineSpec:
        tax = None
        if self.tax_rate is not None:
            tax = TaxSpec(self.tax_rate.pk, self.tax_rate.rate, self.tax_rate.dpp_numerator, self.tax_rate.dpp_denominator)
        return LineSpec(self.quantity, self.unit_price, self.discount_percent, tax)


# --- Validation ---------------------------------------------------------------


def _belongs(obj, organization) -> bool:
    return obj is None or obj.organization_id == organization.pk


def _validate_header(organization, kind: str, contact, invoice_date: date, due_date: date) -> None:
    if not _belongs(contact, organization):
        raise ValidationFailed("Kontak tidak ditemukan.")
    if kind == InvoiceKind.SALES and not contact.is_customer:
        raise ValidationFailed("Kontak bukan pelanggan.")
    if kind == InvoiceKind.PURCHASE and not contact.is_supplier:
        raise ValidationFailed("Kontak bukan pemasok.")
    if due_date < invoice_date:
        raise ValidationFailed("Tanggal jatuh tempo tidak boleh sebelum tanggal faktur.")


def _validate_lines(organization, kind: str, lines: Sequence[InvoiceLineInput]) -> InvoiceTotals:
    for line in lines:
        if not all(_belongs(obj, organization) for obj in (line.account, line.tax_rate, line.item)):
            raise ValidationFailed("Data baris faktur tidak valid untuk organisasi ini.")
        if not line.account.is_active:
            raise ValidationFailed(f"Akun {line.account} tidak aktif.")
        if line.tax_rate is not None and kind == InvoiceKind.SALES and not organization.is_pkp:
            raise ValidationFailed("Hanya Pengusaha Kena Pajak (PKP) yang dapat memungut PPN. Ubah status PKP di pengaturan.")
    return compute_invoice_totals([line.to_spec() for line in lines])


def _ensure_plan_allows_new_invoice(organization) -> None:
    limit = organization.plan.max_invoices_per_month
    if limit is not None and invoices_created_this_month(organization) >= limit:
        raise PlanLimitExceeded(
            f"Batas {limit} faktur per bulan untuk paket {organization.plan.name} telah tercapai. Tingkatkan paket Anda."
        )


# --- Drafts -------------------------------------------------------------------


def _save_lines(invoice: Invoice, lines: Sequence[InvoiceLineInput], totals: InvoiceTotals) -> None:
    InvoiceLine.objects.bulk_create(
        [
            InvoiceLine(
                invoice=invoice,
                item=line.item,
                description=line.description,
                account=line.account,
                quantity=line.quantity,
                unit_price=line.unit_price,
                discount_percent=line.discount_percent,
                tax_rate=line.tax_rate,
                amount=amount,
            )
            for line, amount in zip(lines, totals.line_amounts)
        ]
    )


def _apply_totals(invoice: Invoice, totals: InvoiceTotals) -> None:
    invoice.subtotal = totals.subtotal
    invoice.tax_amount = totals.tax_total
    invoice.total = totals.total


@transaction.atomic
def create_invoice(
    *,
    organization,
    kind: str,
    contact,
    date: date,
    lines: Sequence[InvoiceLineInput],
    due_date: date | None = None,
    reference: str = "",
    tax_invoice_number: str = "",
    notes: str = "",
    created_by=None,
) -> Invoice:
    due_date = due_date or date + timedelta(days=contact.payment_terms_days)
    _validate_header(organization, kind, contact, date, due_date)
    totals = _validate_lines(organization, kind, lines)
    _ensure_plan_allows_new_invoice(organization)
    invoice = Invoice(
        organization=organization,
        kind=kind,
        contact=contact,
        date=date,
        due_date=due_date,
        reference=reference,
        tax_invoice_number=tax_invoice_number,
        notes=notes,
        created_by=created_by,
    )
    _apply_totals(invoice, totals)
    invoice.save()
    _save_lines(invoice, lines, totals)
    return invoice


def _ensure_draft(invoice: Invoice) -> None:
    if invoice.status != Invoice.Status.DRAFT:
        raise InvalidStateTransition("Hanya faktur berstatus draf yang dapat diubah atau dihapus.")


@transaction.atomic
def update_draft_invoice(
    *,
    invoice: Invoice,
    contact,
    date: date,
    lines: Sequence[InvoiceLineInput],
    due_date: date | None = None,
    reference: str = "",
    tax_invoice_number: str = "",
    notes: str = "",
) -> Invoice:
    invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)
    _ensure_draft(invoice)
    organization = invoice.organization
    due_date = due_date or date + timedelta(days=contact.payment_terms_days)
    _validate_header(organization, invoice.kind, contact, date, due_date)
    totals = _validate_lines(organization, invoice.kind, lines)
    invoice.contact, invoice.date, invoice.due_date = contact, date, due_date
    invoice.reference, invoice.tax_invoice_number, invoice.notes = reference, tax_invoice_number, notes
    _apply_totals(invoice, totals)
    invoice.save()
    invoice.lines.all().delete()
    _save_lines(invoice, lines, totals)
    return invoice


@transaction.atomic
def delete_draft_invoice(*, invoice: Invoice) -> None:
    _ensure_draft(invoice)
    invoice.delete()


# --- Issuing & voiding -------------------------------------------------------------


def _amounts_by_account(pairs) -> "OrderedDict[Account, Decimal]":
    grouped: OrderedDict[Account, Decimal] = OrderedDict()
    for account, amount in pairs:
        grouped[account] = grouped.get(account, ZERO) + amount
    return grouped


def _journal_lines(invoice: Invoice, lines: list[InvoiceLine], totals: InvoiceTotals) -> list[JournalLineInput]:
    organization = invoice.organization
    line_amounts = [line.amount for line in lines]

    if invoice.is_sales:
        result = [JournalLineInput(get_system_account(organization, SystemRole.RECEIVABLE), debit=totals.total)]
        for account, amount in _amounts_by_account((l.account, l.amount) for l in lines).items():
            if amount:
                result.append(JournalLineInput(account, credit=amount))
        if totals.tax_total:
            result.append(JournalLineInput(get_system_account(organization, SystemRole.OUTPUT_VAT), credit=totals.tax_total))
        return result

    # Purchases: a PKP may credit input VAT; otherwise VAT becomes part of cost.
    vat_recoverable = organization.is_pkp
    if not vat_recoverable:
        for group in totals.tax_groups:
            shares = allocate(group.tax, [line_amounts[i] for i in group.line_indexes])
            for index, share in zip(group.line_indexes, shares):
                line_amounts[index] += share
    result = [
        JournalLineInput(account, debit=amount)
        for account, amount in _amounts_by_account(zip((l.account for l in lines), line_amounts)).items()
        if amount
    ]
    if vat_recoverable and totals.tax_total:
        result.append(JournalLineInput(get_system_account(organization, SystemRole.INPUT_VAT), debit=totals.tax_total))
    result.append(JournalLineInput(get_system_account(organization, SystemRole.PAYABLE), credit=totals.total))
    return result


@transaction.atomic
def issue_invoice(*, invoice: Invoice, issued_by=None) -> Invoice:
    invoice = Invoice.objects.select_for_update().select_related("organization", "contact").get(pk=invoice.pk)
    _ensure_draft(invoice)
    lines = list(invoice.lines.select_related("account", "tax_rate"))
    totals = _validate_lines(
        invoice.organization,
        invoice.kind,
        [InvoiceLineInput(l.description, l.account, l.quantity, l.unit_price, l.discount_percent, l.tax_rate, l.item) for l in lines],
    )
    if totals.total <= 0:
        raise ValidationFailed("Total faktur harus lebih dari nol.")

    invoice.number = next_document_number(invoice.organization, INVOICE_PREFIX[invoice.kind], invoice.date)
    label = "Penjualan kepada" if invoice.is_sales else "Pembelian dari"
    invoice.journal_entry = create_journal_entry(
        organization=invoice.organization,
        date=invoice.date,
        description=f"{label} {invoice.contact.name}",
        reference=invoice.number,
        source=JOURNAL_SOURCE[invoice.kind],
        lines=_journal_lines(invoice, lines, totals),
        created_by=issued_by,
    )
    _apply_totals(invoice, totals)
    invoice.status = Invoice.Status.ISSUED
    invoice.issued_at = timezone.now()
    invoice.save()
    return invoice


@transaction.atomic
def void_invoice(*, invoice: Invoice, on: date, voided_by=None) -> Invoice:
    invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)
    if invoice.status in (Invoice.Status.PARTIAL, Invoice.Status.PAID):
        raise InvalidStateTransition("Batalkan seluruh pembayaran faktur ini terlebih dahulu.")
    if invoice.status != Invoice.Status.ISSUED:
        raise InvalidStateTransition("Hanya faktur yang sudah diterbitkan yang dapat dibatalkan.")
    reverse_journal_entry(
        entry=invoice.journal_entry, date=max(on, invoice.date), created_by=voided_by,
        description=f"Pembatalan faktur {invoice.number}",
    )
    invoice.status = Invoice.Status.VOID
    invoice.save(update_fields=["status", "updated_at"])
    return invoice


# --- Payments -----------------------------------------------------------------


def _refresh_payment_status(invoice: Invoice) -> None:
    invoice.status = payment_state(invoice.total, invoice.amount_paid)
    invoice.save(update_fields=["amount_paid", "status", "updated_at"])


@transaction.atomic
def record_payment(
    *, invoice: Invoice, date: date, amount: Decimal, cash_account: Account,
    method: str = Payment.Method.TRANSFER, reference: str = "", created_by=None,
) -> Payment:
    invoice = Invoice.objects.select_for_update().select_related("organization", "contact").get(pk=invoice.pk)
    organization = invoice.organization
    if not invoice.is_open:
        raise InvalidStateTransition("Faktur tidak dalam status menunggu pembayaran.")
    if amount <= 0:
        raise ValidationFailed("Jumlah pembayaran harus lebih dari nol.")
    if amount > invoice.balance_due:
        raise ValidationFailed(f"Jumlah pembayaran melebihi sisa tagihan ({invoice.balance_due}).")
    if date < invoice.date:
        raise ValidationFailed("Tanggal pembayaran tidak boleh sebelum tanggal faktur.")
    if not _belongs(cash_account, organization) or not cash_account.is_cash:
        raise ValidationFailed("Pilih akun kas/bank yang valid.")

    number = next_document_number(organization, PAYMENT_PREFIX[invoice.kind], date)
    if invoice.is_sales:
        description = f"Penerimaan dari {invoice.contact.name} atas {invoice.number}"
        lines = [
            JournalLineInput(cash_account, debit=amount),
            JournalLineInput(get_system_account(organization, SystemRole.RECEIVABLE), credit=amount),
        ]
    else:
        description = f"Pembayaran kepada {invoice.contact.name} atas {invoice.number}"
        lines = [
            JournalLineInput(get_system_account(organization, SystemRole.PAYABLE), debit=amount),
            JournalLineInput(cash_account, credit=amount),
        ]
    entry = create_journal_entry(
        organization=organization, date=date, description=description, reference=number,
        source=JournalEntry.Source.PAYMENT, lines=lines, created_by=created_by,
    )
    payment = Payment.objects.create(
        organization=organization, number=number, invoice=invoice, date=date, amount=amount,
        cash_account=cash_account, method=method, reference=reference, journal_entry=entry, created_by=created_by,
    )
    invoice.amount_paid += amount
    _refresh_payment_status(invoice)
    return payment


@transaction.atomic
def void_payment(*, payment: Payment, on: date, voided_by=None) -> Payment:
    payment = Payment.objects.select_for_update().get(pk=payment.pk)
    if payment.status == Payment.Status.VOID:
        raise InvalidStateTransition("Pembayaran sudah dibatalkan.")
    invoice = Invoice.objects.select_for_update().get(pk=payment.invoice_id)
    reverse_journal_entry(
        entry=payment.journal_entry, date=max(on, payment.date), created_by=voided_by,
        description=f"Pembatalan pembayaran {payment.number}",
    )
    payment.status = Payment.Status.VOID
    payment.save(update_fields=["status", "updated_at"])
    invoice.amount_paid -= payment.amount
    _refresh_payment_status(invoice)
    return payment
