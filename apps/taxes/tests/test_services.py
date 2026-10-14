import calendar
from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone, translation

from apps.core.exceptions import InvalidStateTransition, ValidationFailed
from apps.ledger.domain import SystemRole
from apps.ledger.models import Account
from apps.ledger.selectors import account_balance
from apps.ledger.services import JournalLineInput, create_journal_entry
from apps.taxes.domain import Month
from apps.taxes.forms import PphFinalPaymentForm
from apps.taxes.models import PphFinalAccrual, TaxRate
from apps.taxes.selectors import monthly_gross_turnover, pph_final_schedule
from apps.taxes.services import accrue_pph_final, cancel_pph_final_accrual, cancel_pph_final_payment, record_pph_final_payment

D = Decimal


@pytest.fixture(autouse=True)
def english():
    """Messages are matched in English (the default language is Indonesian)."""
    with translation.override("en"):
        yield


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


@pytest.fixture
def cv(other_organization):
    """A CV: no Rp500 juta exemption, so every sale carries PPh Final."""
    return other_organization


def acc_of(organization):
    return lambda code: Account.objects.get(organization=organization, code=code)


def role_balance(organization, role):
    return account_balance(organization, Account.objects.get(organization=organization, system_role=role))


def test_record_and_cancel_pph_final_payment(cv, user):
    acc = acc_of(cv)
    cash_sale(cv, acc, D("20000000"), date(2026, 1, 20))
    assert pph_final_schedule(cv, 2026)[0].tax == D("100000")

    payment = record_pph_final_payment(
        organization=cv, year=2026, month=1, paid_on=date(2026, 2, 10), cash_account=acc("1-1000"), ntpn="ABC123"
    )
    assert payment.journal_entry.description == "Setoran PPh Final UMKM masa 01/2026"
    assert str(payment) == "PPh Final 01/2026"
    # The missing expense was recorded first, in January, and the deposit settled it.
    accrual = payment.accrual
    assert accrual.journal_entry.date == date(2026, 1, 31) and accrual.tax_amount == D("100000")
    assert role_balance(cv, SystemRole.PPH_FINAL_EXPENSE) == D("100000")
    assert role_balance(cv, SystemRole.PPH_FINAL_PAYABLE) == 0
    assert account_balance(cv, acc("1-1000")) == D("19900000")

    with translation.override("id"), pytest.raises(ValidationFailed, match="Januari 2026"):
        record_pph_final_payment(organization=cv, year=2026, month=Month.JANUARY, paid_on=date(2026, 2, 10), cash_account=acc("1-1000"))

    cancel_pph_final_payment(payment=payment, on=date(2026, 2, 11))
    # The expense still belongs to January; only the liability is open again.
    assert role_balance(cv, SystemRole.PPH_FINAL_EXPENSE) == D("100000")
    assert role_balance(cv, SystemRole.PPH_FINAL_PAYABLE) == D("100000")
    again = record_pph_final_payment(organization=cv, year=2026, month=1, paid_on=date(2026, 2, 12), cash_account=acc("1-1000"))
    assert again.accrual == accrual  # settles the same expense
    assert role_balance(cv, SystemRole.PPH_FINAL_PAYABLE) == 0


def test_expense_is_recorded_at_month_end_before_payment(cv, user):
    acc = acc_of(cv)
    cash_sale(cv, acc, D("10000000"), date(2026, 3, 10))
    accrual = accrue_pph_final(organization=cv, year=2026, month=Month.MARCH, created_by=user)
    assert accrual.journal_entry.date == date(2026, 3, 31)
    assert accrual.journal_entry.description == "Beban PPh Final UMKM masa 03/2026"
    assert role_balance(cv, SystemRole.PPH_FINAL_EXPENSE) == D("50000")
    assert role_balance(cv, SystemRole.PPH_FINAL_PAYABLE) == D("50000")  # owed until it is paid

    with pytest.raises(ValidationFailed, match="already been recorded"):
        accrue_pph_final(organization=cv, year=2026, month=Month.MARCH)
    payment = record_pph_final_payment(organization=cv, year=2026, month=3, paid_on=date(2026, 4, 15), cash_account=acc("1-1100"))
    assert payment.accrual == accrual and payment.tax_amount == accrual.tax_amount

    with pytest.raises(InvalidStateTransition, match="payment"):
        cancel_pph_final_accrual(accrual=accrual, on=date(2026, 4, 16))
    cancel_pph_final_payment(payment=payment, on=date(2026, 4, 16))
    cancel_pph_final_accrual(accrual=accrual, on=date(2026, 4, 16))
    assert role_balance(cv, SystemRole.PPH_FINAL_EXPENSE) == 0
    assert role_balance(cv, SystemRole.PPH_FINAL_PAYABLE) == 0


def test_expense_waits_for_the_month_to_end(cv):
    acc = acc_of(cv)
    today = timezone.localdate()
    cash_sale(cv, acc, D("10000000"), today)
    with pytest.raises(ValidationFailed, match="has not ended yet"):
        accrue_pph_final(organization=cv, year=today.year, month=Month(today.month))
    if today.day == calendar.monthrange(today.year, today.month)[1]:
        return  # the last day of the month may already record it
    with pytest.raises(ValidationFailed, match="has not ended yet"):
        record_pph_final_payment(organization=cv, year=today.year, month=today.month, paid_on=today, cash_account=acc("1-1000"))


def test_pph_final_page_records_the_expense(auth_client, organization, account):
    organization.legal_form = organization.LegalForm.CV
    organization.save(update_fields=["legal_form"])
    cash_sale(organization, account, D("2000000"), date(2026, 5, 4))
    response = auth_client.post(reverse("taxes:pph_final_accrue"), {"year": "2026", "month": "5"})
    assert response.status_code == 302
    accrual = PphFinalAccrual.objects.get(organization=organization)
    page = auth_client.get(reverse("taxes:pph_final") + "?year=2026").content.decode()
    assert accrual.journal_entry.number in page

    auth_client.post(reverse("taxes:pph_final_accrue"), {"year": "2026", "month": "13"})
    assert PphFinalAccrual.objects.count() == 1
    auth_client.post(reverse("taxes:pph_final_accrual_cancel", args=[accrual.pk]))
    accrual.refresh_from_db()
    assert accrual.status == PphFinalAccrual.Status.VOID


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
