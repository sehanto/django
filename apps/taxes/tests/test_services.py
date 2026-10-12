from datetime import date
from decimal import Decimal

import pytest
from django.utils import translation

from apps.core.exceptions import ValidationFailed
from apps.ledger.domain import SystemRole
from apps.ledger.models import Account
from apps.ledger.selectors import account_balance
from apps.ledger.services import JournalLineInput, create_journal_entry
from apps.taxes.domain import Month
from apps.taxes.forms import PphFinalPaymentForm
from apps.taxes.models import TaxRate
from apps.taxes.selectors import monthly_gross_turnover, pph_final_schedule
from apps.taxes.services import cancel_pph_final_payment, record_pph_final_payment

D = Decimal


def cash_sale(organization, account, amount, on):
    create_journal_entry(
        organization=organization, date=on, description="Penjualan tunai",
        lines=[JournalLineInput(account("1-1000"), debit=amount), JournalLineInput(account("4-1000"), credit=amount)],
    )


def test_default_tax_rates_seeded(organization):
    rates = TaxRate.objects.for_organization(organization)
    assert rates.count() == 2
    assert {r.effective_rate for r in rates} == {D("11.00"), D("12.00")}


def test_turnover_comes_from_operating_revenue_net_of_returns(organization, account):
    cash_sale(organization, account, D("1000000"), date(2026, 3, 5))
    create_journal_entry(
        organization=organization, date=date(2026, 3, 6), description="Retur",
        lines=[JournalLineInput(account("4-1200"), debit=D("100000")), JournalLineInput(account("1-1000"), credit=D("100000"))],
    )
    create_journal_entry(  # other income is not peredaran bruto usaha
        organization=organization, date=date(2026, 3, 7), description="Bunga",
        lines=[JournalLineInput(account("1-1100"), debit=D("5000")), JournalLineInput(account("4-2100"), credit=D("5000"))],
    )
    assert monthly_gross_turnover(organization, 2026)[2] == D("900000")


def test_record_and_cancel_pph_final_payment(other_organization, user):
    org = other_organization  # CV: no Rp500 juta exemption

    def acc(code):
        return Account.objects.get(organization=org, code=code)

    cash_sale(org, acc, D("20000000"), date(2026, 1, 20))
    assert pph_final_schedule(org, 2026)[0].tax == D("100000")

    payment = record_pph_final_payment(
        organization=org, year=2026, month=1, paid_on=date(2026, 2, 10), cash_account=acc("1-1000"), ntpn="ABC123"
    )
    assert payment.journal_entry.description == "Setoran PPh Final UMKM masa 01/2026"
    assert str(payment) == "PPh Final 01/2026"
    expense = Account.objects.get(organization=org, system_role=SystemRole.PPH_FINAL_EXPENSE)
    assert account_balance(org, expense) == D("100000")
    assert account_balance(org, acc("1-1000")) == D("19900000")

    with translation.override("id"), pytest.raises(ValidationFailed, match="Januari 2026"):
        record_pph_final_payment(organization=org, year=2026, month=Month.JANUARY, paid_on=date(2026, 2, 10), cash_account=acc("1-1000"))

    cancel_pph_final_payment(payment=payment, on=date(2026, 2, 11))
    assert account_balance(org, expense) == 0


def test_individual_below_threshold_owes_nothing(organization, account):
    cash_sale(organization, account, D("20000000"), date(2026, 1, 20))
    with pytest.raises(ValidationFailed):
        record_pph_final_payment(
            organization=organization, year=2026, month=1, paid_on=date(2026, 2, 10), cash_account=account("1-1000")
        )


@pytest.mark.parametrize(("raw", "valid"), [("3", True), ("12", True), ("0", False), ("13", False), ("maret", False)])
def test_payment_form_accepts_only_calendar_months(organization, account, raw, valid):
    form = PphFinalPaymentForm(
        {"month": raw, "paid_on": "2026-04-10", "cash_account": account("1-1000").pk, "ntpn": ""}, organization=organization
    )
    assert form.is_valid() is valid
    if valid:
        assert form.cleaned_data["month"] is Month(int(raw))
