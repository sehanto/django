"""What buyers withhold from what they pay: the PPN a VAT collector deposits itself (and how
returns treat it), and income tax (PPh 22/23 as prepaid tax, PPh Final towards the month's
deposit)."""

from datetime import date
from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import translation

from apps.contacts.models import Contact
from apps.core.exceptions import InvalidStateTransition, ValidationFailed
from apps.invoicing import services
from apps.invoicing.models import IncomeTaxWithheld, Invoice, InvoiceKind, Payment, ReturnNote
from apps.invoicing.selectors import vat_summary
from apps.invoicing.services import IncomeTaxInput, InvoiceLineInput, ReturnLineInput
from apps.ledger.models import Account
from apps.ledger.selectors import account_balance
from apps.ledger.services import JournalLineInput, create_journal_entry
from apps.taxes.models import PphFinalPayment, TaxRate
from apps.taxes.services import cancel_pph_final_payment, record_pph_final_payment

D = Decimal


@pytest.fixture(autouse=True)
def english():
    with translation.override("en"):
        yield


def balance(organization, code):
    return account_balance(organization, Account.objects.get(organization=organization, code=code))


# --- Returns after a VAT collector withheld the PPN ------------------------------------


@pytest.fixture
def collector_invoice(pkp_organization, customer, account):
    customer.npwp, customer.vat_collector = "0001234567890000", Contact.VatCollector.GOVERNMENT
    customer.save()
    ppn = TaxRate.objects.filter(organization=pkp_organization, dpp_numerator=11).first()
    return services.issue_invoice(invoice=services.create_invoice(
        organization=pkp_organization, kind=InvoiceKind.SALES, contact=customer, date=date(2026, 3, 5),
        lines=[InvoiceLineInput("Kopi", account("4-1000"), D("10"), D("120000"), tax_rate=ppn)],  # 1.200.000 + PPN 132.000
    ))


def test_a_return_leaves_out_the_ppn_the_collector_withheld(pkp_organization, account, collector_invoice):
    invoice = collector_invoice
    payment = services.record_payment(
        invoice=invoice, date=date(2026, 3, 20), amount=D("1332000"), cash_account=account("1-1100"), vat_withheld=D("132000"),
    )
    with pytest.raises(ValidationFailed, match="cash or bank account"):  # only the price is refunded
        services.create_return(invoice=invoice, date=date(2026, 3, 25), reason="Rusak", lines=[ReturnLineInput(invoice.lines.get(), D("2"))])
    note = services.create_return(
        invoice=invoice, date=date(2026, 3, 25), reason="Rusak", lines=[ReturnLineInput(invoice.lines.get(), D("2"))],
        cash_account=account("1-1100"),
    )
    # 2 x 120.000 = 240.000, PPN 26.400: the collector deposited that PPN, so only the price comes back.
    assert (note.total, note.tax_amount, note.vat_withheld, note.credited, note.refunded) == (
        D("266400"), D("26400"), D("26400"), 0, D("240000"),
    )
    assert balance(pkp_organization, "1-1100") == D("1200000") - D("240000")
    assert balance(pkp_organization, "2-1100") == 0  # PPN Keluaran: never ours, before or after
    march = vat_summary(pkp_organization, 2026)[2]
    assert (march["output"], march["collected"], march["net"]) == (D("105600"), D("105600"), 0)
    invoice.refresh_from_db()
    assert invoice.vat_withheld == D("105600") and invoice.status == Invoice.Status.PAID

    with pytest.raises(InvalidStateTransition, match="Void those returns first"):
        services.void_payment(payment=payment, on=date(2026, 3, 26))
    services.void_return(note=note, on=date(2026, 3, 26))
    services.void_payment(payment=payment, on=date(2026, 3, 26))
    invoice.refresh_from_db()
    assert invoice.status == Invoice.Status.ISSUED and invoice.vat_withheld == 0


def test_a_return_before_payment_lowers_the_ppn_the_collector_may_withhold(pkp_organization, account, collector_invoice):
    invoice = collector_invoice
    note = services.create_return(invoice=invoice, date=date(2026, 3, 10), reason="Rusak", lines=[ReturnLineInput(invoice.lines.get(), D("2"))])
    assert (note.vat_withheld, note.credited) == (0, D("266400"))  # nothing withheld yet: a plain credit
    invoice.refresh_from_db()
    assert invoice.vat_still_withheld == D("105600") and invoice.balance_due == D("1065600")
    with pytest.raises(ValidationFailed, match="at most"):
        services.record_payment(invoice=invoice, date=date(2026, 3, 20), amount=D("1065600"), cash_account=account("1-1100"), vat_withheld=D("132000"))
    services.record_payment(invoice=invoice, date=date(2026, 3, 20), amount=D("1065600"), cash_account=account("1-1100"), vat_withheld=D("105600"))
    assert balance(pkp_organization, "2-1100") == 0 and balance(pkp_organization, "1-1100") == D("960000")


def test_the_database_keeps_returns_within_the_withheld_ppn(pkp_organization, account, collector_invoice):
    invoice = collector_invoice
    entry = create_journal_entry(
        organization=pkp_organization, date=date(2026, 3, 25), description="x",
        lines=[JournalLineInput(account("4-1000"), debit=D("10")), JournalLineInput(account("1-1200"), credit=D("10"))],
    )
    with pytest.raises(IntegrityError), transaction.atomic():  # nothing was withheld on this invoice
        ReturnNote.objects.create(
            organization=pkp_organization, kind=InvoiceKind.SALES, number="RJ-X", invoice=invoice, date=date(2026, 3, 25),
            reason="x", subtotal=D("10"), tax_amount=D("1"), total=D("11"), credited=D("10"), refunded=0, vat_withheld=D("1"),
            journal_entry=entry,
        )


# --- Income tax withheld by the buyer ---------------------------------------------------


@pytest.fixture
def cv(other_organization):
    """A CV on PPh Final: no Rp500 juta exemption."""
    return other_organization


@pytest.fixture
def cv_invoice(cv):
    def acc(code):
        return Account.objects.get(organization=cv, code=code)

    buyer = Contact.objects.create(organization=cv, kind=Contact.Kind.CUSTOMER, name="Dinas Pendidikan")

    def make(amount="10000000", on=date(2026, 3, 5)):
        return services.issue_invoice(invoice=services.create_invoice(
            organization=cv, kind=InvoiceKind.SALES, contact=buyer, date=on,
            lines=[InvoiceLineInput("Jasa pelatihan", acc("4-1100"), D("1"), D(amount))],
        ))
    make.acc = acc
    return make


def test_pph_22_and_23_withheld_become_prepaid_tax(cv, cv_invoice):
    acc = cv_invoice.acc
    invoice = cv_invoice()
    payment = services.record_payment(
        invoice=invoice, date=date(2026, 3, 20), amount=D("10000000"), cash_account=acc("1-1100"),
        income_tax=IncomeTaxInput(IncomeTaxWithheld.PPH_22, D("150000"), acc("1-1500"), slip="BPPU-0001"),
    )
    assert (payment.income_tax_kind, payment.income_tax_withheld, payment.cash_received) == ("pph22", D("150000"), D("9850000"))
    assert (balance(cv, "1-1100"), balance(cv, "1-1500"), balance(cv, "1-1200")) == (D("9850000"), D("150000"), 0)
    invoice.refresh_from_db()
    assert invoice.status == Invoice.Status.PAID

    other = cv_invoice()
    with pytest.raises(ValidationFailed, match="prepaid tax account"):  # never cash, nor an account postings own
        services.record_payment(
            invoice=other, date=date(2026, 3, 20), amount=D("10000000"), cash_account=acc("1-1100"),
            income_tax=IncomeTaxInput(IncomeTaxWithheld.PPH_23, D("200000"), acc("1-1400")),
        )
    with pytest.raises(ValidationFailed, match="at most"):
        services.record_payment(
            invoice=other, date=date(2026, 3, 20), amount=D("100000"), cash_account=acc("1-1100"),
            income_tax=IncomeTaxInput(IncomeTaxWithheld.PPH_23, D("200000"), acc("1-1500")),
        )


def test_pph_final_withheld_counts_towards_the_months_deposit(cv, cv_invoice):
    acc = cv_invoice.acc
    services.record_payment(
        invoice=cv_invoice(), date=date(2026, 3, 20), amount=D("10000000"), cash_account=acc("1-1100"),
        income_tax=IncomeTaxInput(IncomeTaxWithheld.PPH_FINAL, D("50000")),
    )
    cv_invoice("30000000", date(2026, 3, 25))  # unpaid: its PPh Final is ours to deposit
    assert balance(cv, "2-1200") == D("-50000")  # the payable it settles is accrued at month end

    deposit = record_pph_final_payment(organization=cv, year=2026, month=3, paid_on=date(2026, 4, 10), cash_account=acc("1-1100"))
    # 0.5% of 40.000.000 = 200.000 due, of which the buyer withheld 50.000.
    assert (deposit.tax_amount, deposit.withheld, deposit.deposited) == (D("200000"), D("50000"), D("150000"))
    assert balance(cv, "2-1200") == 0 and balance(cv, "1-1100") == D("10000000") - D("50000") - D("150000")

    late = cv_invoice()
    with pytest.raises(ValidationFailed, match="already been deposited"):
        services.record_payment(
            invoice=late, date=date(2026, 3, 28), amount=D("10000000"), cash_account=acc("1-1100"),
            income_tax=IncomeTaxInput(IncomeTaxWithheld.PPH_FINAL, D("50000")),
        )
    payment = Payment.objects.get(invoice__organization=cv, income_tax_kind=IncomeTaxWithheld.PPH_FINAL)
    with pytest.raises(InvalidStateTransition, match="Cancel that deposit first"):
        services.void_payment(payment=payment, on=date(2026, 4, 11))
    cancel_pph_final_payment(payment=deposit, on=date(2026, 4, 11))
    services.void_payment(payment=payment, on=date(2026, 4, 11))


def test_a_month_covered_by_withholding_has_nothing_to_deposit(cv, cv_invoice):
    acc = cv_invoice.acc
    services.record_payment(
        invoice=cv_invoice(), date=date(2026, 3, 20), amount=D("10000000"), cash_account=acc("1-1100"),
        income_tax=IncomeTaxInput(IncomeTaxWithheld.PPH_FINAL, D("50000")),
    )
    with pytest.raises(ValidationFailed, match="nothing to deposit"):
        record_pph_final_payment(organization=cv, year=2026, month=3, paid_on=date(2026, 4, 10), cash_account=acc("1-1100"))
    assert not PphFinalPayment.objects.filter(organization=cv).exists()


def test_each_side_withholds_its_own_income_taxes(cv, cv_invoice, account, organization, supplier):
    acc = cv_invoice.acc
    cv.uses_pph_final = False
    cv.save(update_fields=["uses_pph_final"])
    with pytest.raises(ValidationFailed, match="PPh Final scheme"):
        services.record_payment(
            invoice=cv_invoice(), date=date(2026, 3, 20), amount=D("10000000"), cash_account=acc("1-1100"),
            income_tax=IncomeTaxInput(IncomeTaxWithheld.PPH_FINAL, D("50000")),
        )
    bill = services.issue_invoice(invoice=services.create_invoice(
        organization=organization, kind=InvoiceKind.PURCHASE, contact=supplier, date=date(2026, 3, 5),
        lines=[InvoiceLineInput("Sewa", account("6-1100"), D("1"), D("1000000"))],
    ))
    with pytest.raises(ValidationFailed, match="PPh 23 or PPh 4"):  # PPh 22 is withheld by buyers, not from suppliers
        services.record_payment(
            invoice=bill, date=date(2026, 3, 20), amount=D("1000000"), cash_account=account("1-1100"),
            income_tax=IncomeTaxInput(IncomeTaxWithheld.PPH_22, base=D("1000000"), rate=D("1.5")),
        )
    entry = create_journal_entry(
        organization=organization, date=date(2026, 3, 20), description="x",
        lines=[JournalLineInput(account("2-1000"), debit=D("10")), JournalLineInput(account("1-1100"), credit=D("10"))],
    )
    with pytest.raises(IntegrityError), transaction.atomic():  # the database agrees
        Payment.objects.create(
            organization=organization, number="X-1", invoice=bill, date=date(2026, 3, 20), amount=D("10"),
            income_tax_kind=IncomeTaxWithheld.PPH_FINAL, income_tax_withheld=D("2"),
            cash_account=account("1-1100"), journal_entry=entry,
        )


def test_the_payment_form_records_income_tax_withheld(auth_client, organization, customer, account):
    invoice = services.issue_invoice(invoice=services.create_invoice(
        organization=organization, kind=InvoiceKind.SALES, contact=customer, date=date(2026, 3, 5),
        lines=[InvoiceLineInput("Jasa", account("4-1100"), D("1"), D("1000000"))],
    ))
    page = auth_client.get(reverse("invoicing:detail", args=[InvoiceKind.SALES, invoice.pk]))
    form = page.context["payment_form"]
    assert form.fields["income_tax_account"].initial == account("1-1500")
    assert [f.name for f in form.income_tax_fields] == ["income_tax_kind", "income_tax_withheld", "income_tax_account", "withholding_slip"]
    post = {
        "date": "2026-03-20", "amount": "1000000", "cash_account": account("1-1100").pk, "method": Payment.Method.TRANSFER,
        "income_tax_kind": IncomeTaxWithheld.PPH_23, "income_tax_withheld": "",
        "income_tax_account": account("1-1500").pk, "withholding_slip": "",
    }
    with translation.override("en"):
        auth_client.post(reverse("invoicing:pay", args=[InvoiceKind.SALES, invoice.pk]), post)
        assert not invoice.payments.exists()  # the amount was missing
        auth_client.post(reverse("invoicing:pay", args=[InvoiceKind.SALES, invoice.pk]), {**post, "income_tax_withheld": "20000", "withholding_slip": "BP-7"})
    payment = invoice.payments.get()
    assert (payment.income_tax_withheld, payment.withholding_slip, payment.cash_received) == (D("20000"), "BP-7", D("980000"))
