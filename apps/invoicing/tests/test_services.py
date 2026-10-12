from datetime import date
from decimal import Decimal

import pytest

from apps.core.exceptions import InvalidStateTransition, PlanLimitExceeded, ValidationFailed
from apps.invoicing import services
from apps.invoicing.models import Invoice, InvoiceKind
from apps.invoicing.services import InvoiceLineInput
from apps.ledger.domain import SystemRole
from apps.ledger.selectors import account_balance
from apps.taxes.models import TaxRate
from apps.tenants.models import Plan

D = Decimal
ON = date(2026, 3, 10)


@pytest.fixture
def ppn(organization):
    return TaxRate.objects.get(organization=organization, name__startswith="PPN 12% (DPP 11/12)")


def sale(organization, customer, account, *, tax_rate=None, qty=D("10"), price=D("100000"), issue=True):
    invoice = services.create_invoice(
        organization=organization,
        kind=InvoiceKind.SALES,
        contact=customer,
        date=ON,
        lines=[InvoiceLineInput("Kopi bubuk 1kg", account("4-1000"), qty, price, tax_rate=tax_rate)],
    )
    return services.issue_invoice(invoice=invoice) if issue else invoice


def journal_lines(invoice):
    return {(l.account.code, l.debit, l.credit) for l in invoice.journal_entry.lines.select_related("account")}


def test_draft_uses_contact_payment_terms_and_has_no_journal(organization, customer, account):
    invoice = sale(organization, customer, account, issue=False)
    assert invoice.status == Invoice.Status.DRAFT
    assert invoice.number == ""
    assert invoice.due_date == date(2026, 3, 24)
    assert invoice.journal_entry is None


def test_issue_sales_invoice_with_ppn_posts_balanced_journal(pkp_organization, customer, account, ppn):
    invoice = sale(pkp_organization, customer, account, tax_rate=ppn)
    assert invoice.number == "INV/2026/03/0001"
    assert (invoice.subtotal, invoice.tax_amount, invoice.total) == (D("1000000"), D("110000"), D("1110000"))
    assert journal_lines(invoice) == {
        ("1-1200", D("1110000"), D("0")),
        ("4-1000", D("0"), D("1000000")),
        ("2-1100", D("0"), D("110000")),
    }


def test_non_pkp_cannot_charge_ppn(organization, customer, account, ppn):
    with pytest.raises(ValidationFailed, match="PKP"):
        sale(organization, customer, account, tax_rate=ppn)


def test_non_pkp_purchase_capitalises_vat_into_cost(organization, supplier, account, ppn):
    bill = services.create_invoice(
        organization=organization, kind=InvoiceKind.PURCHASE, contact=supplier, date=ON,
        lines=[
            InvoiceLineInput("Biji kopi", account("5-1000"), D("1"), D("600000"), tax_rate=ppn),
            InvoiceLineInput("Kemasan", account("6-1400"), D("1"), D("400000"), tax_rate=ppn),
        ],
    )
    bill = services.issue_invoice(invoice=bill)
    assert bill.number == "BILL/2026/03/0001"
    assert journal_lines(bill) == {
        ("5-1000", D("666000"), D("0")),
        ("6-1400", D("444000"), D("0")),
        ("2-1000", D("0"), D("1110000")),
    }


def test_pkp_purchase_records_input_vat(pkp_organization, supplier, account, ppn):
    bill = services.create_invoice(
        organization=pkp_organization, kind=InvoiceKind.PURCHASE, contact=supplier, date=ON,
        lines=[InvoiceLineInput("Biji kopi", account("5-1000"), D("1"), D("1000000"), tax_rate=ppn)],
    )
    bill = services.issue_invoice(invoice=bill)
    assert ("1-1400", D("110000"), D("0")) in journal_lines(bill)


def test_partial_then_full_payment(organization, customer, account, system_account):
    invoice = sale(organization, customer, account)
    services.record_payment(invoice=invoice, date=ON, amount=D("400000"), cash_account=account("1-1100"))
    invoice.refresh_from_db()
    assert invoice.status == Invoice.Status.PARTIAL
    assert invoice.balance_due == D("600000")

    with pytest.raises(ValidationFailed):
        services.record_payment(invoice=invoice, date=ON, amount=D("600001"), cash_account=account("1-1100"))

    payment = services.record_payment(invoice=invoice, date=ON, amount=D("600000"), cash_account=account("1-1100"))
    invoice.refresh_from_db()
    assert invoice.status == Invoice.Status.PAID
    assert payment.number == "KM/2026/03/0002"
    assert account_balance(invoice.organization, system_account(SystemRole.RECEIVABLE)) == 0
    assert account_balance(invoice.organization, account("1-1100")) == D("1000000")

    with pytest.raises(InvalidStateTransition):
        services.record_payment(invoice=invoice, date=ON, amount=D("1"), cash_account=account("1-1100"))


def test_payment_requires_cash_account(organization, customer, account):
    invoice = sale(organization, customer, account)
    with pytest.raises(ValidationFailed):
        services.record_payment(invoice=invoice, date=ON, amount=D("1"), cash_account=account("6-1000"))


def test_void_payment_restores_balance(organization, customer, account):
    invoice = sale(organization, customer, account)
    payment = services.record_payment(invoice=invoice, date=ON, amount=D("1000000"), cash_account=account("1-1000"))
    services.void_payment(payment=payment, on=ON)
    invoice.refresh_from_db()
    assert invoice.status == Invoice.Status.ISSUED
    assert invoice.amount_paid == 0
    assert account_balance(organization, account("1-1000")) == 0
    with pytest.raises(InvalidStateTransition):
        services.void_payment(payment=payment, on=ON)


def test_void_invoice_reverses_journal_and_requires_no_payments(organization, customer, account, system_account):
    invoice = sale(organization, customer, account)
    payment = services.record_payment(invoice=invoice, date=ON, amount=D("1"), cash_account=account("1-1000"))
    with pytest.raises(InvalidStateTransition):
        services.void_invoice(invoice=invoice, on=ON)
    services.void_payment(payment=payment, on=ON)

    services.void_invoice(invoice=invoice, on=ON)
    invoice.refresh_from_db()
    assert invoice.status == Invoice.Status.VOID
    assert invoice.journal_entry.is_reversed
    assert account_balance(organization, system_account(SystemRole.RECEIVABLE)) == 0


def test_only_drafts_can_be_edited_or_deleted(organization, customer, account):
    draft = sale(organization, customer, account, issue=False)
    services.update_draft_invoice(
        invoice=draft, contact=customer, date=ON,
        lines=[InvoiceLineInput("Teh", account("4-1000"), D("2"), D("5000"))],
    )
    draft.refresh_from_db()
    assert draft.total == D("10000")
    issued = services.issue_invoice(invoice=draft)
    with pytest.raises(InvalidStateTransition):
        services.delete_draft_invoice(invoice=issued)
    with pytest.raises(InvalidStateTransition):
        services.issue_invoice(invoice=issued)


def test_contact_kind_and_dates_validated(organization, customer, supplier, account):
    with pytest.raises(ValidationFailed):
        sale(organization, supplier, account)
    with pytest.raises(ValidationFailed):
        services.create_invoice(
            organization=organization, kind=InvoiceKind.SALES, contact=customer, date=ON, due_date=date(2026, 3, 1),
            lines=[InvoiceLineInput("x", account("4-1000"), D("1"), D("1"))],
        )


def test_free_plan_invoice_quota(organization, customer, account):
    organization.plan = Plan.objects.create(code="mini", name="Mini", max_invoices_per_month=2)
    organization.save()
    sale(organization, customer, account, issue=False)
    sale(organization, customer, account, issue=False)
    with pytest.raises(PlanLimitExceeded):
        sale(organization, customer, account, issue=False)
