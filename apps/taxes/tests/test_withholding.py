"""Income tax withheld from suppliers (PPh 23 and 4(2)) and its monthly deposit."""

from datetime import date
from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import translation

from apps.core.exceptions import InvalidStateTransition, ValidationFailed
from apps.invoicing import services
from apps.invoicing.models import IncomeTaxWithheld, InvoiceKind, Payment
from apps.invoicing.services import IncomeTaxInput, InvoiceLineInput, ReturnLineInput
from apps.ledger.selectors import account_balance
from apps.taxes.models import WithholdingDeposit
from apps.taxes.selectors import withholding_due
from apps.taxes.services import cancel_withholding_deposit, record_withholding_deposit

D = Decimal


@pytest.fixture(autouse=True)
def english():
    with translation.override("en"):
        yield


@pytest.fixture
def bill(organization, supplier, account):
    supplier.npwp = "1091031210920664"
    supplier.save()
    return services.issue_invoice(invoice=services.create_invoice(
        organization=organization, kind=InvoiceKind.PURCHASE, contact=supplier, date=date(2026, 3, 1),
        lines=[InvoiceLineInput("Jasa servis mesin", account("6-1700"), D("1"), D("5000000"))],
    ))


def pph23(base="5000000", rate="2"):
    return IncomeTaxInput(IncomeTaxWithheld.PPH_23, base=D(base), rate=D(rate), slip="")


def test_we_withhold_pph_23_and_deposit_it(organization, bill, account):
    payment = services.record_payment(invoice=bill, date=date(2026, 3, 10), amount=D("5000000"), cash_account=account("1-1100"), income_tax=pph23())
    assert (payment.income_tax_withheld, payment.income_tax_base, payment.income_tax_rate) == (D("100000"), D("5000000"), D("2"))
    assert account_balance(organization, account("1-1100")) == D("-4900000")  # the supplier gets the rest
    assert account_balance(organization, account("2-1210")) == D("100000")
    assert withholding_due(organization, 2026) == {("pph23", 3): D("100000")}

    deposit = record_withholding_deposit(
        organization=organization, kind="pph23", year=2026, month=3, paid_on=date(2026, 4, 10), cash_account=account("1-1100"), ntpn="ABC123",
    )
    assert deposit.amount == D("100000") and account_balance(organization, account("2-1210")) == 0
    with pytest.raises(ValidationFailed, match="already been deposited"):
        record_withholding_deposit(organization=organization, kind="pph23", year=2026, month=3, paid_on=date(2026, 4, 10), cash_account=account("1-1100"))
    with pytest.raises(InvalidStateTransition, match="Cancel that deposit first"):
        services.void_payment(payment=payment, on=date(2026, 4, 11))
    cancel_withholding_deposit(deposit=deposit, on=date(2026, 4, 11))
    services.void_payment(payment=payment, on=date(2026, 4, 11))
    with pytest.raises(IntegrityError), transaction.atomic():  # deposits are never deleted
        WithholdingDeposit.objects.filter(pk=deposit.pk).delete()


def test_withholding_is_checked(organization, bill, account):
    with pytest.raises(ValidationFailed, match="PPh 23, PPh 4"):
        services.record_payment(invoice=bill, date=date(2026, 3, 10), amount=D("5000000"), cash_account=account("1-1100"),
                                income_tax=IncomeTaxInput(IncomeTaxWithheld.PPH_FINAL, base=D("1"), rate=D("1")))
    with pytest.raises(ValidationFailed, match="subtotal"):
        services.record_payment(invoice=bill, date=date(2026, 3, 10), amount=D("5000000"), cash_account=account("1-1100"), income_tax=pph23("9000000"))
    with pytest.raises(ValidationFailed, match="No PPh 23"):
        record_withholding_deposit(organization=organization, kind="pph23", year=2026, month=3, paid_on=date(2026, 4, 10), cash_account=account("1-1100"))


def test_a_supplier_refund_gives_back_what_we_withheld(organization, bill, account):
    services.record_payment(invoice=bill, date=date(2026, 3, 10), amount=D("5000000"), cash_account=account("1-1100"), income_tax=pph23())
    note = services.create_return(
        invoice=bill, date=date(2026, 3, 20), reason="Batal sebagian", lines=[ReturnLineInput(bill.lines.get(), D("0.2"))],
        cash_account=account("1-1100"),
    )
    # A fifth refunded: 1.000.000, on which we had withheld 20.000; the supplier pays back 980.000.
    assert (note.income_tax_withheld, note.refunded) == (D("20000"), D("980000"))
    assert account_balance(organization, account("2-1210")) == D("80000")
    assert withholding_due(organization, 2026) == {("pph23", 3): D("80000")}


def test_the_bukti_potong_number_is_recorded_once(auth_client, organization, bill, account):
    payment = services.record_payment(invoice=bill, date=date(2026, 3, 10), amount=D("5000000"), cash_account=account("1-1100"), income_tax=pph23())
    with translation.override("en"):
        auth_client.post(reverse("invoicing:payment_slip", args=[payment.pk]), {"number": "BPPU-00012", "next": "withholding"})
    payment.refresh_from_db()
    assert payment.withholding_slip == "BPPU-00012"
    with pytest.raises(InvalidStateTransition, match="already has"):
        services.record_withholding_slip(payment=payment, number="X")
    with pytest.raises(IntegrityError), transaction.atomic():
        Payment.objects.filter(pk=payment.pk).update(withholding_slip="X")
    page = auth_client.get(reverse("taxes:withholding_month", args=[2026, 3]))
    assert list(page.context["payments"]) == [payment]
    csv = auth_client.get(reverse("taxes:withholding_month_csv", args=[2026, 3])).content.decode("utf-8-sig")
    assert "BPPU-00012" in csv and "100000" in csv
    row = auth_client.get(reverse("taxes:withholding"), {"year": 2026}).context["rows"][0]
    assert row.due == D("100000") and row.due_on == date(2026, 4, 15) and row.overdue  # not deposited, after the 15th


def test_the_payment_form_withholds_from_suppliers(auth_client, organization, bill, account):
    form = auth_client.get(reverse("invoicing:detail", args=[InvoiceKind.PURCHASE, bill.pk])).context["payment_form"]
    assert [f.name for f in form.income_tax_fields] == [
        "income_tax_kind", "income_tax_object", "income_tax_base", "income_tax_rate", "withholding_slip",
    ]
    assert form.initial["income_tax_base"] == D("5000000")
    with translation.override("en"):
        auth_client.post(reverse("invoicing:pay", args=[InvoiceKind.PURCHASE, bill.pk]), {
            "date": "2026-03-10", "amount": "5000000", "cash_account": account("1-1100").pk, "method": Payment.Method.TRANSFER,
            "income_tax_kind": IncomeTaxWithheld.PPH_4_2, "income_tax_object": "28-403-02", "income_tax_base": "5000000",
            "income_tax_rate": "10", "withholding_slip": "",
        })
    payment = bill.payments.get()
    assert (payment.income_tax_kind, payment.income_tax_withheld, payment.income_tax_object) == ("pph4_2", D("500000"), "28-403-02")
    assert account_balance(organization, account("2-1220")) == D("500000")
