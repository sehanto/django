"""Tax allowances (gross-up), benefits in kind, zakat through payroll, severance, income from a
previous employer and tax treaty rates for PPh 26."""

from datetime import date
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.utils import translation

from apps.core.exceptions import InvalidStateTransition, ValidationFailed
from apps.ledger.selectors import account_balance
from apps.payroll import selectors, services
from apps.payroll.domain import TaxScheme, gross_up, monthly_pph21, severance_tax
from apps.payroll.models import Employee
from apps.payroll.services import PayInput

D = Decimal


@pytest.fixture(autouse=True)
def english():
    with translation.override("en"):
        yield


def make(organization, **fields):
    defaults = dict(name="Budi", nik="3174061502560011", ptkp_status="TK/0", start_date=date(2025, 1, 1), base_salary=D("0"),
                    fixed_allowance=D("0"), bpjs_employment=False, bpjs_health=False)
    return Employee.objects.create(organization=organization, **{**defaults, **fields})


def run(organization, month, *pays, year=2026):
    return services.run_payroll(organization=organization, year=year, month=month, pays=list(pays))


def test_the_rules():
    # PP 68/2009: 0% to 50 juta, 5% to 100 juta, 15% to 500 juta, 25% above, on the cumulative amount.
    assert severance_tax(D("150000000")) == D("10000000")
    assert severance_tax(D("60000000"), paid_before=D("90000000")) == D("8000000")  # 10 juta x 5% + 50 juta x 15%
    assert severance_tax(D("600000000")) == D("2500000") + D("60000000") + D("25000000")
    # The DJP's gross-up example (PMK 168): Rp51.827.997 grossed up to Rp65.605.059 at 21%.
    assert gross_up(lambda a: monthly_pph21("TK/0", D("51827997") + a)[1]) == D("13777062")


def test_the_employer_pays_the_tax_as_an_allowance(organization):
    employee = make(organization, tax_borne_by_employer=True)
    slip = run(organization, 8, PayInput(employee, D("51827997"))).payslips.get()
    assert (slip.tax_allowance, slip.taxable_gross, slip.ter_rate, slip.pph21) == (D("13777062"), D("65605059"), D("21"), D("13777062"))
    assert slip.net_pay == D("51827997") and slip.pay == D("65605059")  # take-home pay unchanged


def test_benefits_in_kind_are_taxed_but_not_paid(organization, account):
    employee = make(organization)
    payroll = run(organization, 1, PayInput(employee, D("10000000"), benefits_in_kind=D("2000000")))
    slip = payroll.payslips.get()
    assert (slip.taxable_gross, slip.ter_rate, slip.pph21) == (D("12000000"), D("4"), D("480000"))
    assert slip.net_pay == D("10000000") - D("480000") and payroll.gross == D("10000000")  # booked where it was bought


def test_zakat_through_payroll(organization, account):
    employee = make(organization, zakat=D("250000"))
    with pytest.raises(ValidationFailed, match="zakat payable account"):
        run(organization, 1, PayInput(employee, D("10000000")))
    settings = services.settings_for(organization)
    settings.zakat_payable_account = account("2-1400")
    settings.save()
    for month in range(1, 13):
        run(organization, month, PayInput(employee, D("10000000")))
    assert account_balance(organization, account("2-1400")) == 12 * D("250000")
    st = selectors.annual_statement(employee, 2026)
    # 120 juta - biaya jabatan 6 juta - zakat 3 juta - PTKP 54 juta = 57 juta x 5%.
    assert (st.annual.zakat, st.annual.taxable, st.tax, st.withheld) == (D("3000000"), D("57000000"), D("2850000"), D("2850000"))


def test_severance_bears_final_pph21(organization, account):
    employee = make(organization, end_date=date(2026, 6, 30))
    payroll = run(organization, 6, PayInput(employee, D("10000000"), severance=D("150000000")))
    slip = payroll.payslips.get()
    assert slip.severance_tax == D("10000000")
    assert payroll.pph21 == slip.pph21 + D("10000000")  # deposited with PPh 21
    assert slip.net_pay == D("160000000") - slip.pph21 - D("10000000")
    st = selectors.annual_statement(employee, 2026)
    assert st.gross == D("10000000")  # severance is final income, not on the 1721-A1


def test_severance_paid_in_instalments(organization, account, auth_client):
    from django.urls import reverse

    from apps.taxes import ebupot

    employee = make(organization, start_date=date(2020, 1, 1), end_date=date(2024, 6, 30))
    first = run(organization, 6, PayInput(employee, D("10000000"), severance=D("60000000")), year=2024).payslips.get()
    assert (first.severance_tax, first.severance_final) == (D("500000"), True)  # 10 juta x 5%

    # After they left, only severance: listed in the run form, no PPh 21 period of their own.
    with pytest.raises(ValidationFailed, match="only severance"):
        run(organization, 9, PayInput(employee, D("10000000")), year=2024)
    form = auth_client.get(reverse("payroll:run_create", args=[2024, 9])).context["form"]
    assert [(e, base) for e, base, *_rest in form.rows] == [(employee, None)]
    later = run(organization, 9, PayInput(employee, D("0"), severance=D("50000000")), year=2024).payslips.get()
    # Layers on the cumulative 110 juta: 2,5 juta + 1,5 juta, less the 0,5 juta already withheld.
    assert (later.tax_scheme, later.pph21, later.severance_tax, later.severance_final) == (TaxScheme.SEVERANCE_ONLY, D("0"), D("3500000"), True)
    assert later.net_pay == D("50000000") - D("3500000")
    page = auth_client.get(reverse("payroll:payslip", args=[later.pk]))
    assert page.status_code == 200 and b"PPh 21 (" not in page.content  # no PPh 21 period of their own
    assert auth_client.get(reverse("payroll:run", args=[later.run_id])).status_code == 200
    assert selectors.annual_statement(employee, 2024).gross == D("10000000")  # the 1721-A1 is untouched
    assert run(organization, 2, PayInput(employee, D("0"), severance=D("20000000")), year=2025).payslips.get().severance_tax == D("3000000")

    # The third calendar year: Art. 17 on that year's total, not final (PP 68/2009 Art. 6).
    third = run(organization, 3, PayInput(employee, D("0"), severance=D("70000000")), year=2026).payslips.get()
    assert (third.severance_tax, third.severance_final) == (D("4500000"), False)  # 60 juta x 5% + 10 juta x 15%
    fourth = run(organization, 4, PayInput(employee, D("0"), severance=D("30000000")), year=2026).payslips.get()
    assert fourth.severance_tax == D("4500000")  # 30 juta more at 15%
    organization.npwp = "0123456789012345"
    organization.save(update_fields=["npwp"])
    (row,) = ebupot.month_batches(organization, 2026, 3)["bp21"].records
    fields = dict(row.fields)
    assert (fields["TaxObjectCode"], fields["Gross"], fields["Rate"]) == ("21-100-25", D("70000000"), D("15"))


def test_income_from_a_previous_employer(organization, client, user):
    employee = make(organization, start_date=date(2026, 9, 1))
    services.add_prior_income(employee=employee, year=2026, employer_name="PT Lama", net_income=D("70000000"), pph21=D("700000"),
                              first_month=1, last_month=8, slip_number="A1-123")
    for month in (9, 10, 11, 12):
        run(organization, month, PayInput(employee, D("10000000")))
    st = selectors.annual_statement(employee, 2026)
    # 40 juta - biaya jabatan 2 juta = 38 juta, + 70 juta = 108 juta - PTKP 54 juta = 54 juta x 5% = 2,7 juta.
    assert (st.annual.prior_net, st.annual.taxable, st.tax, st.prior_tax, st.due) == (
        D("70000000"), D("54000000"), D("2700000"), D("700000"), D("2000000"))
    assert st.withheld == st.due
    prior = employee.prior_incomes.get()
    with pytest.raises(InvalidStateTransition, match="settled"):
        services.remove_prior_income(prior=prior)


def test_a_tax_treaty_lowers_pph26(organization):
    employee = make(organization, nationality="SGP", nik="", passport_number="K1", is_resident=False, treaty_rate=D("0"))
    with pytest.raises(ValidationError, match="certificate of residence"):
        employee.full_clean()
    employee.treaty_document = "DGT-2026-01"
    employee.save()
    slip = run(organization, 3, PayInput(employee, D("40000000"))).payslips.get()
    assert (slip.tax_scheme, slip.pph26_rate, slip.pph21) == (TaxScheme.PPH_26, D("0"), D("0"))
