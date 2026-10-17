"""Payroll: BPJS, PPh 21 (TER and the year-end calculation), postings and guards."""

from datetime import date
from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import translation

from apps.core.exceptions import InvalidStateTransition, ValidationFailed
from apps.ledger.models import Account
from apps.ledger.selectors import account_balance
from apps.payroll import services
from apps.payroll.domain import annual_pph21, monthly_pph21
from apps.payroll.models import Employee, PayrollRun, Payslip
from apps.payroll.services import PayInput
from apps.taxes.selectors import withholding_due
from apps.taxes.services import record_withholding_deposit

D = Decimal


@pytest.fixture(autouse=True)
def english():
    with translation.override("en"):
        yield


def test_ter_and_the_annual_calculation():
    assert monthly_pph21("TK/0", D("5400000")) == (D("0"), D("0"))
    assert monthly_pph21("TK/0", D("10000000")) == (D("2"), D("200000"))  # category A, 9.65-10.05 juta
    assert monthly_pph21("K/1", D("10000000")) == (D("1.5"), D("150000"))  # category B, 9.2-10.75 juta
    assert monthly_pph21("K/3", D("11000000")) == (D("1.75"), D("192500"))  # category C, 10.95-11.2 juta
    assert monthly_pph21("TK/0", D("1500000000")) == (D("34"), D("510000000"))
    year = annual_pph21("K/0", D("120000000"), D("3600000"), 12)
    # 120 juta - biaya jabatan 6 juta - JHT/JP 3,6 juta - PTKP 58,5 juta = 51,9 juta x 5%.
    assert (year.taxable, year.tax) == (D("51900000"), D("2595000"))
    # PKP 640 juta: 5% x 60 + 15% x 190 + 25% x 250 + 30% x 140 juta.
    assert annual_pph21("TK/0", D("700000000"), 0, 12).tax == D("3000000") + D("28500000") + D("62500000") + D("42000000")


def acc(organization, code):
    return Account.objects.get(organization=organization, code=code)


@pytest.fixture
def staff(organization):
    return Employee.objects.create(
        organization=organization, name="Budi", nik="3174061502560011", ptkp_status="TK/0", start_date=date(2025, 1, 1),
        base_salary=D("9000000"), fixed_allowance=D("1000000"),
    )


def run(organization, month, employee, base="9000000", allowance="1000000", bonus="0"):
    return services.run_payroll(organization=organization, year=2026, month=month, pays=[PayInput(employee, D(base), D(allowance), D(bonus))])


def test_a_month_is_posted_paid_and_its_pph21_deposited(organization, staff):
    payroll = run(organization, 1, staff)
    slip = payroll.payslips.get()
    # Wage 10 juta: JKK 24.000 + JKM 30.000 + BPJS Kesehatan 400.000 are taxable premiums: gross 10.454.000.
    assert (slip.jkk, slip.jkm, slip.health_employer, slip.taxable_gross) == (D("24000"), D("30000"), D("400000"), D("10454000"))
    assert (slip.ter_rate, slip.pph21) == (D("2.5"), D("261350"))  # category A, 10.35-10.7 juta
    # Employee: JHT 2% 200.000, JP 1% 100.000, health 1% 100.000.
    assert slip.employee_contributions == D("400000") and slip.net_pay == D("10000000") - D("400000") - D("261350")
    assert account_balance(organization, acc(organization, "2-1230")) == D("261350")
    assert account_balance(organization, acc(organization, "2-1240")) == slip.employer_contributions + slip.employee_contributions
    assert withholding_due(organization, 2026)[("pph21", 1)] == D("261350")

    services.pay_salaries(run=payroll, paid_on=date(2026, 1, 31), cash_account=acc(organization, "1-1100"))
    services.pay_bpjs(run=payroll, paid_on=date(2026, 2, 10), cash_account=acc(organization, "1-1100"))
    assert account_balance(organization, acc(organization, "2-1300")) == 0
    with pytest.raises(InvalidStateTransition, match="paid"):
        services.void_payroll(run=payroll, on=date(2026, 2, 11))
    record_withholding_deposit(organization=organization, kind="pph21", year=2026, month=1, paid_on=date(2026, 2, 12), cash_account=acc(organization, "1-1100"))
    assert account_balance(organization, acc(organization, "2-1230")) == 0
    with pytest.raises(ValidationFailed, match="already been recorded"):
        run(organization, 1, staff)


def test_december_settles_the_year(organization, staff):
    withheld = D("0")
    for month in range(1, 12):
        withheld += run(organization, month, staff).pph21
    december = run(organization, 12, staff, bonus="10000000").payslips.get()
    assert december.is_final_period and december.ter_rate is None
    gross = D("10454000") * 12 + D("10000000")
    year = annual_pph21("TK/0", gross, D("300000") * 12, 12)
    assert december.pph21 == year.tax - withheld
    with pytest.raises(InvalidStateTransition, match="Void them first"):
        services.void_payroll(run=PayrollRun.objects.get(organization=organization, month=11), on=date(2026, 12, 31))


def test_an_employee_who_leaves_is_settled_in_their_last_month(organization, staff):
    staff.end_date = date(2026, 3, 15)
    staff.save()
    run(organization, 1, staff)
    run(organization, 2, staff)
    march = run(organization, 3, staff, base="4500000", allowance="0").payslips.get()
    assert march.is_final_period and march.pph21 < 0  # three months' income is under PTKP: a refund
    with pytest.raises(ValidationFailed, match="only severance"):
        run(organization, 4, staff)


def test_the_database_keeps_payrolls(organization, staff):
    payroll = run(organization, 1, staff)
    with pytest.raises(IntegrityError), transaction.atomic():
        PayrollRun.objects.filter(pk=payroll.pk).update(net_pay=1)
    with pytest.raises(IntegrityError), transaction.atomic():
        Payslip.objects.filter(run=payroll).delete()
    slip = payroll.payslips.get()
    services.record_slip_number(payslip=slip, number="BPMP-1")
    with pytest.raises(IntegrityError), transaction.atomic():
        Payslip.objects.filter(pk=slip.pk).update(withholding_slip="X")
    services.void_payroll(run=payroll, on=date(2026, 2, 1))
    assert account_balance(organization, acc(organization, "2-1230")) == 0


def test_the_pages(auth_client, organization, staff):
    with translation.override("en"):
        response = auth_client.post(reverse("payroll:run_create", args=[2026, 2]), {
            f"base_{staff.pk}": "9000000", f"allowance_{staff.pk}": "1000000", f"bonus_{staff.pk}": "0",
        })
    payroll = PayrollRun.objects.get(organization=organization)
    assert response.url == reverse("payroll:run", args=[payroll.pk])
    assert auth_client.get(reverse("payroll:index"), {"year": 2026}).status_code == 200
    csv = auth_client.get(reverse("payroll:run_download", args=[payroll.pk, "csv"])).content.decode("utf-8-sig")
    assert "3174061502560011" in csv and "261350" in csv
    assert auth_client.get(reverse("payroll:payslip", args=[payroll.payslips.get().pk])).status_code == 200
    with translation.override("en"):
        auth_client.post(reverse("payroll:run_action", args=[payroll.pk, "pay"]), {"paid_on": "2026-02-28", "cash_account": acc(organization, "1-1100").pk})
    payroll.refresh_from_db()
    assert payroll.paid_on == date(2026, 2, 28)


def test_an_employee_keeps_notes(auth_client, organization):
    from apps.payroll.forms import EmployeeForm

    data = {"name": "John Smith", "kind": "permanent", "pay_basis": "monthly", "nationality": "GBR", "passport_number": "P1",
            "ptkp_status": "TK/0", "start_date": "2026-01-01", "base_salary": "1", "fixed_allowance": "0", "zakat": "0",
            "gender": "", "address": "10 Downing Street, London", "notes": "Kontrak 2 tahun\nCuti tahunan 12 hari"}
    form = EmployeeForm(data=data, organization=organization)
    assert form.fields["notes"].widget.attrs["rows"] == 2 and form.is_valid(), form.errors
    employee = form.save(commit=False)
    employee.organization = organization
    employee.save()
    assert Employee.objects.get(pk=employee.pk).notes == "Kontrak 2 tahun\nCuti tahunan 12 hari"  # line breaks kept
    page = auth_client.get(reverse("payroll:employee_update", args=[employee.pk])).content.decode()
    assert 'name="notes"' in page and "Cuti tahunan 12 hari" in page
