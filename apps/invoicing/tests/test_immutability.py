"""Issued invoices, their lines, payments and PPh Final payments are immutable in the database.

These tests bypass the services on purpose (ORM shortcuts) to prove the PostgreSQL guards
(invoicing migration 0004, taxes 0006); the legitimate flows go through the services.
"""

from contextlib import contextmanager
from datetime import date
from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction

from apps.contacts.models import Contact
from apps.invoicing import services
from apps.invoicing.models import Invoice, InvoiceKind, InvoiceLine, Item, Payment
from apps.invoicing.services import InvoiceLineInput
from apps.ledger.models import Account
from apps.ledger.services import JournalLineInput, create_journal_entry
from apps.taxes.models import PphFinalPayment, TaxRate
from apps.taxes.services import cancel_pph_final_payment, record_pph_final_payment

D = Decimal
ON = date(2026, 3, 10)


@contextmanager
def refused():
    with pytest.raises(IntegrityError), transaction.atomic():
        yield


@pytest.fixture
def item(organization, account):
    return Item.objects.create(organization=organization, name="Kopi bubuk 1kg", sale_price=D("100000"), income_account=account("4-1000"))


def draft(organization, customer, account, item=None):
    return services.create_invoice(
        organization=organization, kind=InvoiceKind.SALES, contact=customer, date=ON,
        lines=[InvoiceLineInput("Kopi bubuk 1kg", account("4-1000"), D("2"), D("100000"), item=item)],
    )


@pytest.fixture
def issued(organization, customer, account):
    return services.issue_invoice(invoice=draft(organization, customer, account))


def pay(invoice, account, amount):
    return services.record_payment(invoice=invoice, date=ON, amount=D(amount), cash_account=account("1-1000"))


# --- Invoices -------------------------------------------------------------------------


def test_drafts_stay_editable_and_deletable(organization, customer, account):
    invoice = draft(organization, customer, account)
    Invoice.objects.filter(pk=invoice.pk).update(notes="diubah")
    InvoiceLine.objects.filter(invoice=invoice).update(description="Kopi arabika")
    services.delete_draft_invoice(invoice=invoice)
    assert not Invoice.objects.filter(pk=invoice.pk).exists()


def test_invoices_cannot_be_inserted_already_issued(organization, customer):
    with refused():
        Invoice.objects.create(organization=organization, kind=InvoiceKind.SALES, contact=customer, date=ON, due_date=ON,
                               number="INV/X", status=Invoice.Status.ISSUED, total=D("1"))


def test_issued_invoice_document_is_frozen(issued, organization):
    someone_else = Contact.objects.create(organization=organization, name="PT Lain")
    for change in ({"total": D("1")}, {"date": date(2026, 3, 11)}, {"number": "INV/LAIN"}, {"notes": "x"},
                   {"tax_invoice_number": "010.000-26.00000001"}, {"contact": someone_else}):
        with refused():
            Invoice.objects.filter(pk=issued.pk).update(**change)
    with refused():
        issued.delete()
    with refused():
        Invoice.objects.filter(pk=issued.pk).update(status=Invoice.Status.DRAFT)


def test_lines_of_an_issued_invoice_are_frozen(issued, account):
    line = issued.lines.get()
    with refused():
        InvoiceLine.objects.filter(pk=line.pk).update(unit_price=D("1"))
    with refused():
        line.delete()
    with refused():
        InvoiceLine.objects.create(invoice=issued, description="x", account=account("4-1000"), amount=D("1"))


def test_amount_paid_and_status_follow_the_payments(issued, account):
    with refused():
        Invoice.objects.filter(pk=issued.pk).update(amount_paid=issued.total, status=Invoice.Status.PAID)
    pay(issued, account, "100000")
    issued.refresh_from_db()
    assert issued.status == Invoice.Status.PARTIAL
    with refused():
        Invoice.objects.filter(pk=issued.pk).update(status=Invoice.Status.PAID)  # paid not reached


def test_voiding_needs_the_reversal_and_is_final(issued):
    with refused():
        Invoice.objects.filter(pk=issued.pk).update(status=Invoice.Status.VOID)  # journal not reversed
    services.void_invoice(invoice=issued, on=ON)
    issued.refresh_from_db()
    assert issued.status == Invoice.Status.VOID
    with refused():
        Invoice.objects.filter(pk=issued.pk).update(status=Invoice.Status.ISSUED)


def test_full_payment_lifecycle_still_works(issued, account):
    first = pay(issued, account, "100000")
    rest = pay(issued, account, str(issued.total - D("100000")))
    issued.refresh_from_db()
    assert issued.status == Invoice.Status.PAID
    services.void_payment(payment=rest, on=ON)
    services.void_payment(payment=first, on=ON)
    services.void_invoice(invoice=issued, on=ON)
    issued.refresh_from_db()
    assert (issued.status, issued.amount_paid) == (Invoice.Status.VOID, 0)


# --- Payments -------------------------------------------------------------------------


def test_payments_are_frozen_and_only_voided_after_reversal(issued, account):
    payment = pay(issued, account, "100000")
    for change in ({"amount": D("1")}, {"date": date(2026, 3, 11)}, {"cash_account": account("1-1100")}):
        with refused():
            Payment.objects.filter(pk=payment.pk).update(**change)
    with refused():
        Payment.objects.filter(pk=payment.pk).update(status=Payment.Status.VOID)  # journal not reversed
    with refused():
        payment.delete()
    services.void_payment(payment=payment, on=ON)
    payment.refresh_from_db()
    with refused():
        Payment.objects.filter(pk=payment.pk).update(status=Payment.Status.POSTED)


def test_payments_need_an_open_invoice_and_a_posted_entry(organization, customer, account, issued):
    a_draft = draft(organization, customer, account)
    entry = pay(issued, account, "1000").journal_entry
    for invoice, status in ((a_draft, Payment.Status.POSTED), (issued, Payment.Status.VOID)):
        with refused():
            Payment.objects.create(organization=organization, number="X", invoice=invoice, date=ON, amount=D("1"),
                                   cash_account=account("1-1000"), status=status, journal_entry=entry)


# --- PPh Final payments ---------------------------------------------------------------


@pytest.fixture
def pph_payment(other_organization):
    org = other_organization  # CV: no Rp500 juta exemption

    def acc(code):
        return Account.objects.get(organization=org, code=code)

    create_journal_entry(
        organization=org, date=date(2026, 1, 20), description="Penjualan tunai",
        lines=[JournalLineInput(acc("1-1000"), debit=D("20000000")), JournalLineInput(acc("4-1000"), credit=D("20000000"))],
    )
    return record_pph_final_payment(organization=org, year=2026, month=1, paid_on=date(2026, 2, 10), cash_account=acc("1-1000"))


def test_pph_final_payments_are_cancelled_not_deleted(pph_payment):
    with refused():
        PphFinalPayment.objects.filter(pk=pph_payment.pk).update(tax_amount=D("1"))
    with refused():
        pph_payment.delete()
    cancel_pph_final_payment(payment=pph_payment, on=date(2026, 2, 11))
    pph_payment.refresh_from_db()
    assert pph_payment.status == PphFinalPayment.Status.VOID  # still there, as history

    again = record_pph_final_payment(  # the period can be paid again after a cancellation
        organization=pph_payment.organization, year=2026, month=1, paid_on=date(2026, 2, 12), cash_account=pph_payment.cash_account,
    )
    assert again.status == PphFinalPayment.Status.POSTED
    assert PphFinalPayment.objects.filter(organization=pph_payment.organization, year=2026, month=1).count() == 2


# --- Reference data: snapshot + lock when used ----------------------------------------


def test_issued_invoices_keep_the_parties_as_they_were(client, auth_client, organization, customer, account, issued):
    from django.urls import reverse

    customer.name, customer.address = "PT Pelanggan Baru", "Jl. Baru 1"
    customer.save()  # contacts stay editable...
    organization.name = "Usaha Ganti Nama"
    organization.save()

    issued.refresh_from_db()
    assert issued.party_name == "PT Pelanggan" and issued.seller["name"] != "Usaha Ganti Nama"  # ...issued invoices don't change
    page = auth_client.get(reverse("invoicing:print", args=[InvoiceKind.SALES, issued.pk])).content.decode()
    assert "PT Pelanggan" in page and "PT Pelanggan Baru" not in page and "Usaha Ganti Nama" not in page

    later = draft(organization, customer, account)
    assert later.party_name == "PT Pelanggan Baru"  # drafts show the current details
    with refused():
        Invoice.objects.filter(pk=issued.pk).update(contact_name="Siapa Saja")


def test_issuing_requires_the_party_snapshot(organization, customer, account):
    invoice = draft(organization, customer, account)
    entry = services.issue_invoice(invoice=draft(organization, customer, account)).journal_entry
    with refused():  # an issue that skipped the snapshot
        Invoice.objects.filter(pk=invoice.pk).update(status=Invoice.Status.ISSUED, number="INV/X", total=D("1"), journal_entry=entry)


def test_items_used_on_issued_invoices_cannot_be_deleted(organization, customer, account, item):
    services.issue_invoice(invoice=draft(organization, customer, account, item=item))
    item.name = "Kopi bubuk 500g"
    item.save()  # still editable: lines copied what they needed
    with refused():
        item.delete()
    unused = Item.objects.create(organization=organization, name="Teh", sale_price=D("1"))
    unused.delete()


def test_tax_rates_used_on_issued_invoices_are_locked(pkp_organization, customer, account):
    organization = pkp_organization
    from apps.taxes.forms import TaxRateForm

    ppn = TaxRate.objects.get(organization=organization, name__startswith="PPN 12% (DPP 11/12)")
    form = TaxRateForm(instance=ppn, organization=organization)
    assert not form.locked and not form.fields["rate"].disabled  # unused: editable

    services.issue_invoice(invoice=services.create_invoice(
        organization=organization, kind=InvoiceKind.SALES, contact=customer, date=ON,
        lines=[InvoiceLineInput("Kopi", account("4-1000"), D("1"), D("100000"), tax_rate=ppn)],
    ))
    with refused():
        TaxRate.objects.filter(pk=ppn.pk).update(rate=D("11"))
    with refused():
        TaxRate.objects.filter(pk=ppn.pk).update(name="PPN lama")
    TaxRate.objects.filter(pk=ppn.pk).update(is_active=False)  # deactivating is allowed
    form = TaxRateForm(instance=ppn, organization=organization)
    assert form.locked and form.fields["rate"].disabled and not form.fields["is_active"].disabled
    with refused():
        ppn.delete()


def test_issued_lines_keep_the_account_as_named_at_issue(organization, customer, account):
    revenue = account("4-1000")
    invoice = draft(organization, customer, account)
    assert invoice.lines.get().account_code == ""  # drafts follow the chart
    Account.objects.filter(pk=revenue.pk).update(name="Penjualan Kopi")
    services.issue_invoice(invoice=invoice)
    line = invoice.lines.get()
    assert (line.account_code, line.account_name) == ("4-1000", "Penjualan Kopi")

    Account.objects.filter(pk=revenue.pk).update(name="Pendapatan Lain", code="4-1001")
    line.refresh_from_db()
    assert line.account_label == "4-1000 · Penjualan Kopi"
    with refused():
        InvoiceLine.objects.filter(pk=line.pk).update(account_name="Akun Lain")


def test_issued_lines_keep_the_tax_rate_as_it_was(pkp_organization, customer, account):
    organization = pkp_organization
    ppn = TaxRate.objects.get(organization=organization, name__startswith="PPN 12% (DPP 11/12)")
    taxed = services.create_invoice(
        organization=organization, kind=InvoiceKind.SALES, contact=customer, date=ON,
        lines=[InvoiceLineInput("Kopi", account("4-1000"), D("1"), D("100000"), tax_rate=ppn)],
    )
    assert taxed.lines.get().tax_name == "" and taxed.lines.get().tax_label == str(ppn)  # drafts: current rate
    services.issue_invoice(invoice=taxed)
    line = taxed.lines.get()
    assert (line.tax_name, line.tax_rate_percent, line.tax_dpp_numerator, line.tax_dpp_denominator) == (ppn.name, D("12.00"), 11, 12)
    assert line.tax_label == ppn.name
    with refused():
        InvoiceLine.objects.filter(pk=line.pk).update(tax_rate_percent=D("11"))

    untaxed = services.issue_invoice(invoice=draft(organization, customer, account))
    plain = untaxed.lines.get()
    assert (plain.tax_name, plain.tax_rate_percent, plain.tax_label) == ("", None, "")
