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
from django.utils.translation import gettext as _
from django.utils.translation import ngettext

from apps.core.exceptions import InvalidStateTransition, PlanLimitExceeded, ValidationFailed
from apps.core.formatting import localized_rupiah
from apps.core.money import ZERO
from apps.core.sequences import next_document_number
from apps.core.validators import ensure_business_date
from apps.inventory import services as inventory
from apps.ledger.domain import SystemRole
from apps.ledger.models import Account, JournalEntry
from apps.ledger.services import JournalLineInput, create_journal_entry, get_system_account, reverse_journal_entry

from .domain import InvoiceTotals, LineSpec, TaxSpec, allocate, compute_invoice_totals, payment_state
from .models import Invoice, InvoiceKind, InvoiceLine, Item, Payment, ReturnLine, ReturnNote
from .selectors import invoices_created_this_month

INVOICE_PREFIX = {InvoiceKind.SALES: "INV", InvoiceKind.PURCHASE: "BILL"}
RETURN_PREFIX = {InvoiceKind.SALES: "RJ", InvoiceKind.PURCHASE: "RB"}
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
    ensure_business_date(invoice_date)
    ensure_business_date(due_date)
    if not _belongs(contact, organization):
        raise ValidationFailed(_("Contact not found."))
    if kind == InvoiceKind.SALES and not contact.is_customer:
        raise ValidationFailed(_("This contact is not a customer."))
    if kind == InvoiceKind.PURCHASE and not contact.is_supplier:
        raise ValidationFailed(_("This contact is not a supplier."))
    if due_date < invoice_date:
        raise ValidationFailed(_("The due date cannot be before the invoice date."))


def _validate_lines(organization, kind: str, lines: Sequence[InvoiceLineInput]) -> InvoiceTotals:
    for line in lines:
        if not all(_belongs(obj, organization) for obj in (line.account, line.tax_rate, line.item)):
            raise ValidationFailed(_("Invoice line data is not valid for this organization."))
        if not line.account.is_active:
            raise ValidationFailed(_("Account %(account)s is inactive.") % {"account": line.account})
        if line.tax_rate is not None and kind == InvoiceKind.SALES and not organization.is_pkp:
            raise ValidationFailed(_("Only a VAT-registered business (PKP) can charge PPN. Change the PKP status in the settings."))
        if line.item is not None and line.item.track_inventory:
            if line.quantity <= 0:
                raise ValidationFailed(_("The quantity of %(item)s must be positive: its stock is tracked.") % {"item": line.item.name})
            if kind == InvoiceKind.PURCHASE and line.account != line.item.inventory_account:
                raise ValidationFailed(
                    _("Purchases of %(item)s go to its inventory account %(account)s.") % {"item": line.item.name, "account": line.item.inventory_account}
                )
    return compute_invoice_totals([line.to_spec() for line in lines])


def _ensure_plan_allows_new_invoice(organization) -> None:
    limit = organization.plan.max_invoices_per_month
    if limit is not None and invoices_created_this_month(organization) >= limit:
        raise PlanLimitExceeded(
            ngettext(
                "The %(plan)s plan's limit of %(limit)s invoice per month has been reached. Please upgrade your plan.",
                "The %(plan)s plan's limit of %(limit)s invoices per month has been reached. Please upgrade your plan.",
                limit,
            )
            % {"plan": organization.plan.name, "limit": limit}
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
        raise InvalidStateTransition(_("Only draft invoices can be changed or deleted."))


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


def purchase_costs(organization, lines: list[InvoiceLine], totals: InvoiceTotals) -> list[Decimal]:
    """What each purchase line costs the business: a PKP credits input VAT; otherwise the VAT
    is part of the cost (and so of the inventory value)."""
    amounts = [line.amount for line in lines]
    if not organization.is_pkp:
        for group in totals.tax_groups:
            shares = allocate(group.tax, [amounts[i] for i in group.line_indexes])
            for index, share in zip(group.line_indexes, shares):
                amounts[index] += share
    return amounts


def _journal_lines(invoice: Invoice, lines: list[InvoiceLine], totals: InvoiceTotals, stock: list) -> list[JournalLineInput]:
    organization = invoice.organization

    if invoice.is_sales:
        result = [JournalLineInput(get_system_account(organization, SystemRole.RECEIVABLE), debit=totals.total)]
        for account, amount in _amounts_by_account((l.account, l.amount) for l in lines).items():
            if amount:
                result.append(JournalLineInput(account, credit=amount))
        if totals.tax_total:
            result.append(JournalLineInput(get_system_account(organization, SystemRole.OUTPUT_VAT), credit=totals.tax_total))
        return result + inventory.cost_journal_lines(stock)  # cost of goods sold of tracked items

    vat_recoverable = organization.is_pkp
    line_amounts = purchase_costs(organization, lines, totals)
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
    lines = list(invoice.lines.select_related("account", "tax_rate", "item__inventory_account", "item__cogs_account"))
    totals = _validate_lines(
        invoice.organization,
        invoice.kind,
        [InvoiceLineInput(l.description, l.account, l.quantity, l.unit_price, l.discount_percent, l.tax_rate, l.item) for l in lines],
    )
    if totals.total <= 0:
        raise ValidationFailed(_("The invoice total must be greater than zero."))

    if invoice.is_sales:
        stock = inventory.plan_sale(invoice.organization, lines)
    else:
        stock = inventory.plan_purchase(zip(lines, purchase_costs(invoice.organization, lines, totals)))

    invoice.number = next_document_number(invoice.organization, INVOICE_PREFIX[invoice.kind], invoice.date)
    # Journal descriptions are stored business records and stay in Indonesian.
    label = "Penjualan kepada" if invoice.is_sales else "Pembelian dari"
    invoice.journal_entry = create_journal_entry(
        organization=invoice.organization,
        date=invoice.date,
        description=f"{label} {invoice.contact.name}",
        reference=invoice.number,
        source=JOURNAL_SOURCE[invoice.kind],
        lines=_journal_lines(invoice, lines, totals, stock),
        created_by=issued_by,
    )
    inventory.record(stock, entry=invoice.journal_entry, on=invoice.date, created_by=issued_by)
    _apply_totals(invoice, totals)
    invoice.snapshot_parties()  # frozen with the invoice from here on
    invoice.status = Invoice.Status.ISSUED
    invoice.issued_at = timezone.now()
    invoice.save()
    return invoice


@transaction.atomic
def void_invoice(*, invoice: Invoice, on: date, voided_by=None) -> Invoice:
    invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)
    if invoice.returns.filter(status=ReturnNote.Status.POSTED).exists():
        raise InvalidStateTransition(_("This invoice has returns, so it can't be voided. Record a return for the rest instead."))
    if invoice.status in (Invoice.Status.PARTIAL, Invoice.Status.PAID):
        raise InvalidStateTransition(_("Void all payments on this invoice first."))
    if invoice.status != Invoice.Status.ISSUED:
        raise InvalidStateTransition(_("Only issued invoices can be voided."))
    stock = inventory.plan_sale_void(invoice) if invoice.is_sales else inventory.plan_purchase_void(invoice)
    reversal = reverse_journal_entry(
        entry=invoice.journal_entry, date=max(on, invoice.date), created_by=voided_by,
        description=f"Pembatalan faktur {invoice.number}",
    )
    inventory.record(stock, entry=reversal, on=reversal.date, created_by=voided_by)
    invoice.status = Invoice.Status.VOID
    invoice.save(update_fields=["status", "updated_at"])
    return invoice


# --- Payments -----------------------------------------------------------------


def _refresh_payment_status(invoice: Invoice) -> None:
    """Payments and returns credited together settle the invoice (the database checks both)."""
    invoice.status = payment_state(invoice.total, invoice.amount_paid + invoice.amount_credited)
    invoice.save(update_fields=["amount_paid", "amount_credited", "status", "updated_at"])


@transaction.atomic
def record_payment(
    *, invoice: Invoice, date: date, amount: Decimal, cash_account: Account,
    method: str = Payment.Method.TRANSFER, reference: str = "", created_by=None,
) -> Payment:
    invoice = Invoice.objects.select_for_update().select_related("organization", "contact").get(pk=invoice.pk)
    organization = invoice.organization
    if not invoice.is_open:
        raise InvalidStateTransition(_("This invoice is not awaiting payment."))
    if amount <= 0:
        raise ValidationFailed(_("The payment amount must be greater than zero."))
    if amount > invoice.balance_due:
        raise ValidationFailed(
            _("The payment amount exceeds the balance due (%(balance)s).") % {"balance": localized_rupiah(invoice.balance_due)}
        )
    if date < invoice.date:
        raise ValidationFailed(_("The payment date cannot be before the invoice date."))
    if not _belongs(cash_account, organization) or not cash_account.is_cash:
        raise ValidationFailed(_("Choose a valid cash or bank account."))

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
        raise InvalidStateTransition(_("The payment is already voided."))
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


# --- Returns (nota retur) ----------------------------------------------------------


@dataclass(frozen=True)
class ReturnLineInput:
    invoice_line: InvoiceLine
    quantity: Decimal


def returnable_quantity(line: InvoiceLine) -> Decimal:
    returned = sum((r.quantity for r in line.returns.all() if r.return_note.status == ReturnNote.Status.POSTED), Decimal("0"))
    return line.quantity - returned


def _issued_spec(line: InvoiceLine, quantity: Decimal) -> LineSpec:
    """The line as issued: its price, discount and the tax rate frozen at issue."""
    tax = None
    if line.tax_rate_percent is not None:
        key = (line.tax_rate_percent, line.tax_dpp_numerator, line.tax_dpp_denominator)
        tax = TaxSpec(key, line.tax_rate_percent, line.tax_dpp_numerator, line.tax_dpp_denominator)
    return LineSpec(quantity, line.unit_price, line.discount_percent, tax)


@dataclass(frozen=True)
class _ReturnedLine:  # what purchase_costs() needs
    amount: Decimal


@transaction.atomic
def create_return(
    *, invoice: Invoice, date: date, reason: str, lines: Sequence[ReturnLineInput], cash_account: Account | None = None,
    created_by=None,
) -> ReturnNote:
    """Record goods (or services) taken back on an issued invoice: a sales return from a
    customer or a purchase return to a supplier.

    Lines are priced and taxed as issued. The total first reduces what is still owed on the
    invoice; the rest is refunded through ``cash_account``. The PPN is reversed in the
    return's period (nota retur). Tracked stock comes back at the cost it left at (sales) or
    leaves at its current cost (purchases, the difference to cost of goods sold).
    """
    invoice = Invoice.objects.select_for_update().select_related("organization", "contact").get(pk=invoice.pk)
    organization = invoice.organization
    if not invoice.can_return:
        raise InvalidStateTransition(_("Only issued invoices can have returns."))
    ensure_business_date(date)
    if date < invoice.date:
        raise ValidationFailed(_("The return date cannot be before the invoice date."))
    if not reason.strip():
        raise ValidationFailed(_("Give the reason for the return."))
    lines = [line for line in lines if line.quantity]
    if not lines:
        raise ValidationFailed(_("Enter the quantity returned for at least one line."))
    for line in lines:
        if line.invoice_line.invoice_id != invoice.pk:
            raise ValidationFailed(_("Return lines must come from this invoice."))
        available = returnable_quantity(line.invoice_line)
        if line.quantity < 0 or line.quantity > available:
            raise ValidationFailed(
                _("At most %(quantity)s of %(item)s can still be returned.")
                % {"quantity": f"{available.normalize():f}", "item": line.invoice_line.description}
            )

    totals = compute_invoice_totals([_issued_spec(l.invoice_line, l.quantity) for l in lines])
    credited = min(totals.total, max(invoice.balance_due, ZERO))
    refunded = totals.total - credited
    if refunded and (not _belongs(cash_account, organization) or cash_account is None or not cash_account.is_cash):
        raise ValidationFailed(
            _("%(amount)s has already been settled and is refunded: choose the cash or bank account.")
            % {"amount": localized_rupiah(refunded)}
        )

    pairs = [(l.invoice_line, l.quantity) for l in lines]
    stock = inventory.plan_sales_return(pairs) if invoice.is_sales else inventory.plan_purchase_return(organization, pairs)
    journal_lines = _return_journal_lines(invoice, lines, totals, credited, refunded, cash_account, stock)

    number = next_document_number(organization, RETURN_PREFIX[invoice.kind], date)
    label = "Retur penjualan" if invoice.is_sales else "Retur pembelian"  # stored business records stay Indonesian
    entry = create_journal_entry(
        organization=organization, date=date, description=f"{label} {invoice.number} {invoice.contact.name}"[:255],
        reference=number, source=JOURNAL_SOURCE[invoice.kind], lines=journal_lines, created_by=created_by,
    )
    note = ReturnNote.objects.create(
        organization=organization, kind=invoice.kind, number=number, invoice=invoice, date=date, reason=reason.strip(),
        subtotal=totals.subtotal, tax_amount=totals.tax_total, total=totals.total, credited=credited, refunded=refunded,
        cash_account=cash_account if refunded else None, journal_entry=entry, created_by=created_by,
    )
    created = {}
    for line, amount in zip(lines, totals.line_amounts):
        created[line.invoice_line.pk] = ReturnLine.objects.create(
            return_note=note, invoice_line=line.invoice_line, quantity=line.quantity, amount=amount,
        )
    for planned in stock:
        planned.return_line = created[planned.invoice_line.pk]
    inventory.record(stock, entry=entry, on=date, created_by=created_by)

    invoice.amount_credited += credited
    _refresh_payment_status(invoice)
    return note


def _return_journal_lines(invoice, lines, totals, credited, refunded, cash_account, stock) -> list[JournalLineInput]:
    organization = invoice.organization
    if invoice.is_sales:
        result = [
            JournalLineInput(account, debit=amount)
            for account, amount in _amounts_by_account(
                (l.invoice_line.account, amount) for l, amount in zip(lines, totals.line_amounts)
            ).items() if amount
        ]
        if totals.tax_total:
            result.append(JournalLineInput(get_system_account(organization, SystemRole.OUTPUT_VAT), debit=totals.tax_total))
        if credited:
            result.append(JournalLineInput(get_system_account(organization, SystemRole.RECEIVABLE), credit=credited))
        if refunded:
            result.append(JournalLineInput(cash_account, credit=refunded))
        return result + inventory.cost_journal_lines(stock)

    # Purchases: what each line cost (with PPN a non-PKP can't credit) goes back out of its
    # account; tracked stock leaves at its stock cost and the difference goes to cost of goods sold.
    costs = purchase_costs(organization, [_ReturnedLine(a) for a in totals.line_amounts], totals)
    stock_cost = {p.invoice_line.pk: p for p in stock}
    result = []
    if credited:
        result.append(JournalLineInput(get_system_account(organization, SystemRole.PAYABLE), debit=credited))
    if refunded:
        result.append(JournalLineInput(cash_account, debit=refunded))
    credits = []
    for line, cost in zip(lines, costs):
        planned = stock_cost.get(line.invoice_line.pk)
        if planned is None:
            credits.append((line.invoice_line.account, cost))
            continue
        credits.append((planned.item.inventory_account, planned.cost))
        credits.append((planned.item.cogs_account, cost - planned.cost))
    for account, amount in _amounts_by_account(credits).items():
        if amount > 0:
            result.append(JournalLineInput(account, credit=amount))
        elif amount < 0:
            result.append(JournalLineInput(account, debit=-amount))
    if organization.is_pkp and totals.tax_total:
        result.append(JournalLineInput(get_system_account(organization, SystemRole.INPUT_VAT), credit=totals.tax_total))
    return result


@transaction.atomic
def void_return(*, note: ReturnNote, on: date, voided_by=None) -> ReturnNote:
    """Undo a return: its journal entry is reversed (with any refund), the invoice owes the
    credited amount again, and its stock goes back the way it came."""
    note = ReturnNote.objects.select_for_update().get(pk=note.pk)
    if note.status != ReturnNote.Status.POSTED:
        raise InvalidStateTransition(_("This return has already been voided."))
    invoice = Invoice.objects.select_for_update().get(pk=note.invoice_id)
    stock = inventory.plan_return_void(note)
    reversal = reverse_journal_entry(
        entry=note.journal_entry, date=max(on, note.date), created_by=voided_by,
        description=f"Pembatalan retur {note.number}",  # stored business records stay Indonesian
    )
    inventory.record(stock, entry=reversal, on=reversal.date, created_by=voided_by)
    note.status = ReturnNote.Status.VOID  # kept as history: never deleted
    note.save(update_fields=["status", "updated_at"])
    invoice.amount_credited -= note.credited
    _refresh_payment_status(invoice)
    return note
