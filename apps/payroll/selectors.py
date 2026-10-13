"""Reads: the annual PPh 21 statement (bukti potong 1721-A1) of permanent employees."""

from dataclasses import dataclass
from decimal import Decimal

from apps.core.money import ZERO

from .domain import AnnualTax, EmployeeKind, TaxScheme, with_surcharge
from .models import Employee, PayrollRun, Payslip
from .services import year_end_tax


@dataclass(frozen=True)
class AnnualStatement:
    """The figures of form 1721-A1 (numbered as on the form) from the year's payslips."""

    employee: Employee
    year: int
    first_month: int
    last_month: int
    final_slip: Payslip | None  # None while the year (or employment) hasn't ended
    salary: Decimal  # 1
    allowances: Decimal  # 3
    premiums: Decimal  # 5: JKK, JKM and BPJS Kesehatan paid by the employer
    bonus: Decimal  # 7
    annual: AnnualTax
    tax: Decimal  # 18 / 20, with the 20% surcharge without an NPWP
    withheld: Decimal  # 21

    @property
    def gross(self) -> Decimal:  # 8
        return self.salary + self.allowances + self.premiums + self.bonus

    @property
    def deductions(self) -> Decimal:  # 12
        return self.annual.occupational_costs + self.annual.contributions

    @property
    def is_final(self) -> bool:
        return self.final_slip is not None

    @property
    def surcharged(self) -> bool:
        return self.final_slip is not None and self.final_slip.without_tin


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
    return AnnualStatement(
        employee=employee, year=year, first_month=slips[0].run.month, last_month=slips[-1].run.month, final_slip=final,
        salary=sum((s.base_salary for s in slips), ZERO), allowances=sum((s.allowances for s in slips), ZERO),
        premiums=sum((s.jkk + s.jkm + s.health_employer for s in slips), ZERO), bonus=sum((s.bonus for s in slips), ZERO),
        annual=annual, tax=tax, withheld=sum((s.pph21 for s in slips), ZERO),
    )


def annual_statements(organization, year: int) -> list[AnnualStatement]:
    employees = Employee.objects.for_organization(organization).filter(
        kind=EmployeeKind.PERMANENT, payslips__run__year=year, payslips__run__status=PayrollRun.Status.POSTED,
    ).distinct()
    return [s for s in (annual_statement(e, year) for e in employees) if s is not None]
