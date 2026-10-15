"""Reads: who is paid in a month, the annual PPh 21 statement (bukti potong 1721-A1) of permanent employees."""

from dataclasses import dataclass
from decimal import Decimal

from apps.core.money import ZERO
from apps.core.periods import month_end, month_start

from .domain import AnnualTax, EmployeeKind, TaxScheme, annual_pph21, with_surcharge
from .models import Employee, PayrollRun, Payslip, PriorIncome


@dataclass(frozen=True)
class AnnualStatement:
    """The figures of form 1721-A1 (numbered as on the form) from the year's payslips."""

    employee: Employee
    year: int
    first_month: int
    last_month: int
    final_slip: Payslip | None  # None while the year (or employment) hasn't ended
    salary: Decimal  # 1
    tax_allowance: Decimal  # 2
    allowances: Decimal  # 3
    premiums: Decimal  # 5: JKK, JKM and BPJS Kesehatan paid by the employer
    benefits_in_kind: Decimal  # 6
    bonus: Decimal  # 7
    annual: AnnualTax
    tax: Decimal  # 18, with the 20% surcharge without an NPWP
    prior_tax: Decimal  # 19: withheld by earlier employers of the year
    withheld: Decimal  # 21

    @property
    def gross(self) -> Decimal:  # 8
        return self.salary + self.tax_allowance + self.allowances + self.premiums + self.benefits_in_kind + self.bonus

    @property
    def deductions(self) -> Decimal:  # 12
        return self.annual.occupational_costs + self.annual.contributions + self.annual.zakat

    @property
    def due(self) -> Decimal:  # 20
        return self.tax - self.prior_tax

    @property
    def is_final(self) -> bool:
        return self.final_slip is not None

    @property
    def surcharged(self) -> bool:
        return self.final_slip is not None and self.final_slip.without_tin


def prior_income(employee: Employee, year: int) -> tuple[Decimal, Decimal]:
    """(net income, PPh 21) from earlier employers' 1721-A1 of the year."""
    rows = PriorIncome.objects.filter(employee=employee, year=year)
    return sum((r.net_income for r in rows), ZERO), sum((r.pph21 for r in rows), ZERO)


def year_end_tax(employee: Employee, year: int, slips: list[Payslip], *, annualize: bool) -> AnnualTax:
    """The year's PPh 21 of a permanent employee from their payslips of the year (the last one
    included) and what earlier employers reported: Art. 17 on the year's net income less PTKP
    (status of the last payslip)."""
    slips = [s for s in slips if s.tax_scheme in (TaxScheme.TER_MONTHLY, TaxScheme.ANNUAL)]
    gross = sum((s.taxable_gross for s in slips), ZERO)
    pension = sum((s.jht_employee + s.jp_employee for s in slips), ZERO)
    zakat = sum((s.zakat for s in slips), ZERO)
    prior_net, _prior_tax = prior_income(employee, year)
    return annual_pph21(slips[-1].ptkp_status, gross, pension, len(slips), annualize=annualize, zakat=zakat, prior_net=prior_net)


def employees_paid_in(organization, year: int, month: int) -> list[Employee]:
    """Employees on the payroll of a month: employed during it (the active ones, or those who left
    in it), and former employees still receiving severance in instalments."""
    start, end = month_start(year, month), month_end(year, month)
    return [
        e for e in Employee.objects.for_organization(organization).filter(start_date__lte=end)
        if (e.end_date is None or e.end_date >= start) and (e.is_active or e.end_date)
        or e.is_active and e.left_before(year, month)
    ]


def _slips(employee: Employee, year: int):
    return list(
        Payslip.objects.filter(
            employee=employee, run__year=year, run__status=PayrollRun.Status.POSTED,
            tax_scheme__in=[TaxScheme.TER_MONTHLY, TaxScheme.ANNUAL],
        ).select_related("run").order_by("run__month")
    )


def annual_statement(employee: Employee, year: int) -> AnnualStatement | None:
    slips = _slips(employee, year)
    if not slips:
        return None
    final = next((s for s in slips if s.tax_scheme == TaxScheme.ANNUAL), None)
    annual = year_end_tax(employee, year, slips, annualize=final.annualized if final else False)
    tax = with_surcharge(annual.tax, has_tin=not (final is not None and final.without_tin))

    def total(field):
        return sum((getattr(s, field) for s in slips), ZERO)

    return AnnualStatement(
        employee=employee, year=year, first_month=slips[0].run.month, last_month=slips[-1].run.month, final_slip=final,
        salary=total("base_salary"), tax_allowance=total("tax_allowance"), allowances=total("allowances"),
        premiums=sum((s.jkk + s.jkm + s.health_employer for s in slips), ZERO), benefits_in_kind=total("benefits_in_kind"),
        bonus=total("bonus"), annual=annual, tax=tax, prior_tax=prior_income(employee, year)[1], withheld=total("pph21"),
    )


def annual_statements(organization, year: int) -> list[AnnualStatement]:
    employees = Employee.objects.for_organization(organization).filter(
        kind=EmployeeKind.PERMANENT, payslips__run__year=year, payslips__run__status=PayrollRun.Status.POSTED,
    ).distinct()
    return [s for s in (annual_statement(e, year) for e in employees) if s is not None]


def posted_runs(organization, year: int) -> dict[int, PayrollRun]:
    """{month: the posted payroll run} of a year."""
    runs = PayrollRun.objects.for_organization(organization).filter(year=year, status=PayrollRun.Status.POSTED)
    return {run.month: run for run in runs}


def posted_run(organization, year: int, month: int) -> PayrollRun | None:
    return posted_runs(organization, year).get(month)


def has_active_employees(organization) -> bool:
    return Employee.objects.for_organization(organization).filter(is_active=True).exists()
