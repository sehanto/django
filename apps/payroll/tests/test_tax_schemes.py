"""PPh 21 beyond permanent employees (PMK 168/2023): daily and non-permanent workers, fees to
non-employees, the 20% surcharge without an NPWP, PPh 26 for non-residents, annualisation for
people moving to or leaving Indonesia, and the 1721-A1 statement."""

from datetime import date
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import translation

from apps.core.exceptions import ValidationFailed
from apps.invoicing import services as invoicing
from apps.invoicing.models import IncomeTaxWithheld, InvoiceKind
from apps.invoicing.services import IncomeTaxInput, InvoiceLineInput
from apps.ledger.selectors import account_balance
from apps.payroll import selectors, services
from apps.payroll.domain import TaxScheme, annual_pph21, daily_pph21, non_employee_pph21, pph26, with_surcharge
from apps.payroll.models import Employee
from apps.payroll.services import PayInput
from apps.taxes.selectors import withholding_due
from apps.taxes.services import record_withholding_deposit

D = Decimal


@pytest.fixture(autouse=True)
def english():
    with translation.override("en"):
        yield


def test_the_rules_follow_the_djp_examples():
    # Daily workers (PMK 168 examples): 4,5 juta for 10 days is Rp450.000 a day: 0%.
    assert daily_pph21(D("4500000"), 10) == (TaxScheme.TER_DAILY, D("0"), D("0"))
    assert daily_pph21(D("6000000"), 10) == (TaxScheme.TER_DAILY, D("0.5"), D("30000"))
    # Rp3 juta in a day: 5% x 50% x 3 juta.
    assert daily_pph21(D("3000000"), 1) == (TaxScheme.DAILY_ARTICLE_17, None, D("75000"))
    # A lawyer's fee of 400 juta: 5% x 60 juta + 15% x 140 juta on half of it.
    assert non_employee_pph21(D("400000000")) == D("24000000")
    # AC maintenance: 10 juta less the electrician's wage and the parts (4,5 + 1 juta).
    assert non_employee_pph21(D("4500000")) == D("112500")
    assert non_employee_pph21(D("4500000"), has_tin=False) == D("135000")
    assert with_surcharge(D("-100"), has_tin=False) == D("-100")  # refunds aren't surcharged
    assert pph26(D("40000000")) == D("8000000")


def test_annualising_for_part_of_the_year():
    # TK/0 from September: 4 x 15,5 juta, biaya jabatan 4 x 500.000.
    plain = annual_pph21("TK/0", D("62000000"), D("0"), 4)
    assert (plain.net, plain.taxable, plain.tax) == (D("60000000"), D("6000000"), D("300000"))
    moved = annual_pph21("TK/0", D("62000000"), D("0"), 4, annualize=True)
    # 60 juta x 12/4 = 180 juta - PTKP 54 juta = 126 juta: 3 juta + 15% x 66 juta = 12,9 juta, x 4/12.
    assert (moved.annual_net, moved.taxable, moved.annual_tax, moved.tax) == (D("180000000"), D("126000000"), D("12900000"), D("4300000"))


def test_who_needs_which_identity(organization):
    employee = Employee(organization=organization, name="Ana", start_date=date(2026, 1, 1), base_salary=1, fixed_allowance=0)
    with pytest.raises(ValidationError, match="NIK of an Indonesian citizen"):
        employee.full_clean()
    employee.nationality = "AUS"
    with pytest.raises(ValidationError, match="passport number"):
        employee.full_clean()
    employee.passport_number = "PA1234567"
    employee.full_clean()
    assert employee.tin == ""  # a foreigner's passport isn't a tax ID
    employee.npwp = "0912345678901234"
    assert employee.tin == "0912345678901234"


def make(organization, **fields):
    defaults = dict(name="Pekerja", nik="3174061502560012", ptkp_status="TK/0", start_date=date(2025, 1, 1), base_salary=D("0"), fixed_allowance=D("0"),
                    bpjs_employment=False, bpjs_health=False)
    return Employee.objects.create(organization=organization, **{**defaults, **fields})


def test_daily_workers_and_non_residents_in_a_run(organization, account):
    daily = make(organization, name="Harian", kind=Employee.Kind.NON_PERMANENT, pay_basis=Employee.PayBasis.DAILY, base_salary=D("300000"))
    casual = make(organization, name="Pemetik", kind=Employee.Kind.NON_PERMANENT, nik="3174061502560013")
    expat = make(organization, name="Smith", nationality="GBR", nik="", passport_number="P123", is_resident=False)
    with pytest.raises(ValidationFailed, match="days Harian worked"):
        services.run_payroll(organization=organization, year=2026, month=12, pays=[PayInput(daily, D("6000000"))])
    run = services.run_payroll(organization=organization, year=2026, month=12, pays=[
        PayInput(daily, D("6000000"), days=10), PayInput(casual, D("7000000")), PayInput(expat, D("40000000")),
    ])
    slips = {s.employee_id: s for s in run.payslips.all()}
    assert (slips[daily.pk].tax_scheme, slips[daily.pk].pph21, slips[daily.pk].days_worked) == (TaxScheme.TER_DAILY, D("30000"), 10)
    # Non-permanent workers paid monthly keep the monthly TER in December (1,25% on 7 juta).
    assert (slips[casual.pk].tax_scheme, slips[casual.pk].is_final_period, slips[casual.pk].pph21) == (TaxScheme.TER_MONTHLY, False, D("87500"))
    assert (slips[expat.pk].tax_scheme, slips[expat.pk].pph21, slips[expat.pk].without_tin) == (TaxScheme.PPH_26, D("8000000"), False)
    assert (run.pph21, run.pph26) == (D("117500"), D("8000000"))
    assert account_balance(organization, account("2-1250")) == D("8000000")
    assert withholding_due(organization, 2026) == {("pph21", 12): D("117500"), ("pph26", 12): D("8000000")}
    record_withholding_deposit(organization=organization, kind="pph26", year=2026, month=12, paid_on=date(2027, 1, 10), cash_account=account("1-1100"))
    assert account_balance(organization, account("2-1250")) == 0
    with pytest.raises(Exception, match="PPh 26"):
        services.void_payroll(run=run, on=date(2027, 1, 11))


def test_a_foreign_resident_without_npwp_pays_20_percent_more(organization):
    expat = make(organization, name="Tanaka", nationality="JPN", nik="", passport_number="TZ1", base_salary=D("10000000"))
    run = services.run_payroll(organization=organization, year=2026, month=3, pays=[PayInput(expat, D("10000000"))])
    slip = run.payslips.get()
    assert (slip.ter_rate, slip.without_tin, slip.pph21) == (D("2"), True, D("240000"))  # 2% x 10 juta x 120%


def test_leaving_indonesia_annualises_the_last_month_and_the_1721_a1(organization, client, user):
    expat = make(organization, name="Brown", nationality="AUS", nik="", passport_number="PB1", npwp="0912345678901234",
                 end_date=date(2026, 4, 30), leaves_indonesia=True)
    for month in (1, 2, 3, 4):
        services.run_payroll(organization=organization, year=2026, month=month, pays=[PayInput(expat, D("15500000"))])
    last = expat.payslips.get(run__month=4)
    assert (last.tax_scheme, last.annualized) == (TaxScheme.ANNUAL, True)
    # 4 x 15,5 juta annualised, as above: 4,3 juta for the year; 7% TER withheld in January to March.
    assert last.pph21 == D("4300000") - 3 * D("1085000")
    st = selectors.annual_statement(expat, 2026)
    assert (st.is_final, st.gross, st.annual.annual_net, st.tax, st.withheld) == (True, D("62000000"), D("180000000"), D("4300000"), D("4300000"))
    client.force_login(user)
    page = client.get(reverse("payroll:a1", args=[2026, expat.pk]))
    assert page.status_code == 200 and "× 4/12" in page.content.decode()
    listing = client.get(reverse("payroll:annual_csv", args=[2026])).content.decode("utf-8-sig")
    assert "Brown" in listing and "4300000" in listing


def test_a_fee_to_an_individual_withholds_pph_21(organization, supplier, account):
    supplier.id_type, supplier.id_number = "National ID", "3174061502560099"
    supplier.save()
    bill = invoicing.issue_invoice(invoice=invoicing.create_invoice(
        organization=organization, kind=InvoiceKind.PURCHASE, contact=supplier, date=date(2026, 8, 1),
        lines=[InvoiceLineInput("Jasa konsultan", account("6-1700"), D("1"), D("10000000"))],
    ))
    payment = invoicing.record_payment(
        invoice=bill, date=date(2026, 8, 5), amount=D("10000000"), cash_account=account("1-1100"),
        income_tax=IncomeTaxInput(IncomeTaxWithheld.PPH_21, base=D("10000000")),
    )
    assert (payment.income_tax_withheld, payment.income_tax_rate) == (D("250000"), None)  # 5% x 5 juta
    assert account_balance(organization, account("2-1230")) == D("250000")
    assert withholding_due(organization, 2026) == {("pph21", 8): D("250000")}


def test_the_run_form_asks_daily_workers_for_their_days(organization, client, user):
    daily = make(organization, name="Harian", kind=Employee.Kind.NON_PERMANENT, pay_basis=Employee.PayBasis.DAILY, base_salary=D("300000"))
    client.force_login(user)
    page = client.get(reverse("payroll:run_create", args=[2026, 3]))
    assert page.status_code == 200 and f'name="days_{daily.pk}"' in page.content.decode()
    response = client.post(reverse("payroll:run_create", args=[2026, 3]), {f"days_{daily.pk}": "12", f"allowance_{daily.pk}": "0", f"bonus_{daily.pk}": "0"})
    slip = daily.payslips.get()
    assert response.status_code == 302 and (slip.base_salary, slip.days_worked, slip.pph21) == (D("3600000"), 12, D("0"))
