"""Payroll use cases: a month's run posts salaries, BPJS and PPh 21; paying the salaries and
the BPJS are separate entries; the PPh 21 is deposited with the other withheld taxes
(``taxes.WithholdingDeposit``, kind PPh 21)."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.db import transaction
from django.utils.translation import gettext as _

from apps.core.exceptions import InvalidStateTransition, ValidationFailed
from apps.core.money import ZERO
from apps.core.periods import month_end
from apps.core.validators import ensure_business_date, ensure_document_number
from apps.ledger.domain import AccountCategory, SystemRole
from apps.ledger.models import Account, JournalEntry
from apps.ledger.services import JournalLineInput, create_journal_entry, get_system_account, reverse_journal_entry

from .domain import (
    PPH_26_RATE, TER_CATEGORY, AnnualTax, EmployeeKind, PtkpStatus, TaxScheme, annual_pph21, contributions, daily_pph21, gross_up,
    late_severance_tax, monthly_pph21, pph26, severance_tax, with_surcharge,
)
from .models import Employee, PayrollRun, PayrollSettings, Payslip, PriorIncome

DEFAULT_ACCOUNTS = {"salary_expense_account": "6-1000", "benefits_expense_account": "6-1000", "salaries_payable_account": "2-1300"}


def settings_for(organization) -> PayrollSettings:
    """The organization's payroll settings, created with the default chart's accounts."""
    existing = PayrollSettings.objects.filter(organization=organization).first()
    if existing is not None:
        return existing
    accounts = {}
    for field, code in DEFAULT_ACCOUNTS.items():
        account = Account.objects.for_organization(organization).filter(code=code).first()
        if account is None:
            raise ValidationFailed(_("Set up the payroll accounts first (Payroll → Settings)."))
        accounts[field] = account
    return PayrollSettings.objects.create(organization=organization, **accounts)


@transaction.atomic
def save_settings(*, organization, **fields) -> PayrollSettings:
    for name in DEFAULT_ACCOUNTS:
        account = fields[name]
        if account.organization_id != organization.pk or not account.is_active:
            raise ValidationFailed(_("Choose one of your active accounts."))
    if fields["salaries_payable_account"].category != AccountCategory.CURRENT_LIABILITY:
        raise ValidationFailed(_("Salaries payable must be a current liability account."))
    zakat = fields.get("zakat_payable_account")
    if zakat is not None and (zakat.organization_id != organization.pk or zakat.category != AccountCategory.CURRENT_LIABILITY or zakat.system_role):
        raise ValidationFailed(_("Zakat payable must be one of your current liability accounts."))
    settings, _created = PayrollSettings.objects.update_or_create(organization=organization, defaults=fields)
    return settings


def is_final_period(employee: Employee, year: int, month: int) -> bool:
    """For a permanent, resident employee: December, or the month they leave. PPh 21 is then
    computed for the year."""
    if employee.kind != EmployeeKind.PERMANENT or not employee.is_resident or employee.left_before(year, month):
        return False
    end = employee.end_date
    return month == 12 or (end is not None and (end.year, end.month) == (year, month))


def annualizes(employee: Employee, year: int, month: int) -> bool:
    """Tax residence began after January or ends before December: the year-end tax is computed
    on annualised income (PMK 168/2023)."""
    since, end = employee.resident_since, employee.end_date
    arrived = since is not None and since.year == year and since.month > 1
    leaving = employee.leaves_indonesia and end is not None and (end.year, end.month) == (year, month) and month < 12
    return arrived or leaving


@dataclass(frozen=True)
class PayInput:
    employee: Employee
    base_salary: Decimal
    allowances: Decimal = ZERO
    bonus: Decimal = ZERO  # bonus, THR and other irregular pay (no BPJS on it)
    days: int | None = None  # days worked, for workers paid by the day
    benefits_in_kind: Decimal = ZERO  # taxable value of natura (already booked where it was bought)
    severance: Decimal = ZERO  # uang pesangon: final PPh 21 (PP 68/2009)


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


def _earlier_slips(employee: Employee, year: int, month: int):
    return list(Payslip.objects.filter(
        employee=employee, run__year=year, run__month__lt=month, run__status=PayrollRun.Status.POSTED,
    ).order_by("run__month"))


def _severance_tax(employee: Employee, year: int, amount: Decimal, has_tin: bool) -> tuple[Decimal, bool]:
    """(PPh 21, final?) on severance paid in ``year``. PP 68/2009: what is paid within two
    calendar years bears the final layers on the cumulative amount; what is paid from the third
    calendar year on bears the Art. 17 rates on each year's total, not final."""
    paid = Payslip.objects.filter(employee=employee, run__status=PayrollRun.Status.POSTED, run__year__lte=year, severance__gt=0)
    first = min(paid.values_list("run__year", flat=True), default=year)
    if year >= first + 2:
        before = sum((p.severance for p in paid.filter(run__year=year)), ZERO)
        return with_surcharge(late_severance_tax(amount, paid_before=before), has_tin), False
    return severance_tax(amount, paid_before=sum((p.severance for p in paid), ZERO)), True


def compute_payslip(settings: PayrollSettings, employee: Employee, year: int, month: int, pay: PayInput) -> Payslip:
    """The employee's payslip for the month (unsaved): BPJS, PPh 21 (or PPh 26), a tax allowance
    when the employer bears the tax, zakat, and net pay."""
    if min(pay.base_salary, pay.allowances, pay.bonus, pay.benefits_in_kind, pay.severance) < 0:
        raise ValidationFailed(_("Pay amounts can't be negative."))
    status = PtkpStatus(employee.ptkp_status)
    wage = pay.base_salary + pay.allowances
    c = contributions(wage, settings.rates(), employment=employee.bpjs_employment, health=employee.bpjs_health)
    base_gross = wage + pay.bonus + pay.benefits_in_kind + c.taxable_premiums
    has_tin = bool(employee.tin)
    slip = Payslip(
        employee=employee, ptkp_status=status, base_salary=pay.base_salary, allowances=pay.allowances, bonus=pay.bonus,
        benefits_in_kind=pay.benefits_in_kind, severance=pay.severance,
        zakat=employee.zakat if wage + pay.bonus > 0 else ZERO,
        jkk=c.jkk, jkm=c.jkm, jht_employer=c.jht_employer, jp_employer=c.jp_employer, health_employer=c.health_employer,
        jht_employee=c.jht_employee, jp_employee=c.jp_employee, health_employee=c.health_employee,
        ter_category=TER_CATEGORY[status], is_final_period=is_final_period(employee, year, month),
        without_tin=not has_tin and employee.is_resident,
    )
    if employee.is_daily and (not pay.days or pay.days < 1 or pay.days > 31):
        raise ValidationFailed(_("Enter the days %(name)s worked in the month.") % {"name": employee.name})
    earlier = _earlier_slips(employee, year, month) if slip.is_final_period else []

    def assess(allowance: Decimal) -> Decimal:
        """Set the slip's tax for pay including ``allowance``; return the tax."""
        slip.tax_allowance, slip.taxable_gross = allowance, base_gross + allowance
        gross = slip.taxable_gross
        if employee.left_before(year, month) and employee.is_resident:
            slip.tax_scheme, slip.ter_rate, slip.pph21 = TaxScheme.SEVERANCE_ONLY, None, ZERO  # their PPh 21 periods are over
        elif not employee.is_resident:
            rate = employee.treaty_rate if employee.treaty_rate is not None else PPH_26_RATE
            slip.tax_scheme, slip.ter_rate, slip.pph26_rate = TaxScheme.PPH_26, None, rate
            slip.pph21 = pph26(gross + pay.severance, rate)  # PP 68/2009 is for residents
        elif employee.is_daily:
            slip.days_worked = pay.days
            slip.tax_scheme, slip.ter_rate, tax = daily_pph21(gross, pay.days)
            slip.pph21 = with_surcharge(tax, has_tin)
        elif slip.is_final_period:
            slip.tax_scheme, slip.ter_rate, slip.annualized = TaxScheme.ANNUAL, None, annualizes(employee, year, month)
            annual = year_end_tax(employee, year, [*earlier, slip], annualize=slip.annualized)
            withheld = sum((p.pph21 for p in earlier if p.tax_scheme == TaxScheme.TER_MONTHLY), ZERO)
            _prior_net, prior_tax = prior_income(employee, year)
            slip.pph21 = with_surcharge(annual.tax, has_tin) - prior_tax - withheld  # negative: a refund
        else:
            slip.ter_rate, tax = monthly_pph21(status, gross)
            slip.tax_scheme, slip.pph21 = TaxScheme.TER_MONTHLY, with_surcharge(tax, has_tin)
        return slip.pph21

    assess(gross_up(assess) if employee.tax_borne_by_employer else ZERO)
    if pay.severance and employee.is_resident:
        slip.severance_tax, slip.severance_final = _severance_tax(employee, year, pay.severance, has_tin)
    slip.net_pay = wage + pay.bonus + slip.tax_allowance + pay.severance - c.employee - slip.pph21 - slip.severance_tax - slip.zakat
    return slip


def _ensure_pph21_open(organization, year: int, month: int, *, pph26: bool = False) -> None:
    from apps.taxes.models import WithholdingDeposit  # taxes reads payroll lazily too

    kinds = [WithholdingDeposit.Kind.PPH_21] + ([WithholdingDeposit.Kind.PPH_26] if pph26 else [])
    for kind in WithholdingDeposit.objects.for_organization(organization).filter(
        kind__in=kinds, year=year, month=month, status=WithholdingDeposit.Status.POSTED,
    ).values_list("kind", flat=True):
        raise InvalidStateTransition(
            _("The %(tax)s for %(period)s has already been deposited. Cancel that deposit first.")
            % {"tax": WithholdingDeposit.Kind(kind).label, "period": f"{month:02d}/{year}"}
        )


def _payable(organization, year: int, month: int, pays: list[PayInput]) -> list[PayInput]:
    """The pays of a run: one per employee of the organization working that month, a former
    employee receiving only severance; pays without any amount are left out."""
    on = month_end(year, month)
    pays = [p for p in pays if p.base_salary or p.allowances or p.bonus or p.benefits_in_kind or p.severance]
    if not pays:
        raise ValidationFailed(_("Enter the pay of at least one employee."))
    if len({p.employee.pk for p in pays}) != len(pays):
        raise ValidationFailed(_("Each employee is paid once per run."))
    for pay in pays:
        employee = pay.employee
        if employee.organization_id != organization.pk:
            raise ValidationFailed(_("Employee not found."))
        if employee.start_date > on:
            raise ValidationFailed(_("%(name)s doesn't work here in %(period)s.") % {"name": employee.name, "period": f"{month:02d}/{year}"})
        if employee.left_before(year, month) and (pay.base_salary or pay.allowances or pay.bonus or pay.benefits_in_kind):
            raise ValidationFailed(
                _("%(name)s left on %(date)s: only severance can still be paid to them.")
                % {"name": employee.name, "date": f"{employee.end_date:%d/%m/%Y}"}
            )
    return pays


@dataclass(frozen=True)
class RunTotals:
    pay: Decimal
    employer: Decimal
    employee: Decimal
    pph21: Decimal  # negative: a year-end refund larger than the month's withholding
    pph26: Decimal
    zakat: Decimal
    net: Decimal

    @classmethod
    def of(cls, slips: list[Payslip]) -> "RunTotals":
        def total(values):
            return sum(values, ZERO)

        return cls(
            pay=total(s.pay for s in slips),
            employer=total(s.employer_contributions for s in slips),
            employee=total(s.employee_contributions for s in slips),
            pph21=total(s.tax_withheld for s in slips if not s.is_pph26),
            pph26=total(s.pph21 for s in slips if s.is_pph26),
            zakat=total(s.zakat for s in slips),
            net=total(s.net_pay for s in slips),
        )


def _run_lines(organization, settings: PayrollSettings, totals: RunTotals) -> list[JournalLineInput]:
    pph21_account = get_system_account(organization, SystemRole.PPH_21_PAYABLE)
    lines = [
        JournalLineInput(settings.salary_expense_account, debit=totals.pay),
        JournalLineInput(settings.benefits_expense_account, debit=totals.employer),
        JournalLineInput(pph21_account, credit=totals.pph21) if totals.pph21 >= 0 else JournalLineInput(pph21_account, debit=-totals.pph21),
        JournalLineInput(get_system_account(organization, SystemRole.PPH_26_PAYABLE), credit=totals.pph26),
        JournalLineInput(get_system_account(organization, SystemRole.BPJS_PAYABLE), credit=totals.employer + totals.employee),
        JournalLineInput(settings.salaries_payable_account, credit=totals.net),
    ]
    if totals.zakat:
        lines.append(JournalLineInput(settings.zakat_payable_account, credit=totals.zakat))
    return [line for line in lines if line.debit or line.credit]


@transaction.atomic
def run_payroll(*, organization, year: int, month: int, pays: list[PayInput], created_by=None) -> PayrollRun:
    """Post a month's payroll on its last day: salaries and the employer's BPJS as expenses,
    the PPh 21 and BPJS withheld or owed as liabilities, the net pay as salaries payable."""
    if not 1 <= month <= 12:
        raise ValidationFailed(_("Choose a month."))
    on = month_end(year, month)
    ensure_business_date(on)
    if PayrollRun.objects.for_organization(organization).filter(year=year, month=month, status=PayrollRun.Status.POSTED).exists():
        raise ValidationFailed(_("The payroll for %(period)s has already been recorded.") % {"period": f"{month:02d}/{year}"})
    _ensure_pph21_open(organization, year, month)
    pays = _payable(organization, year, month, pays)
    settings = settings_for(organization)
    slips = [compute_payslip(settings, pay.employee, year, month, pay) for pay in pays]
    totals = RunTotals.of(slips)
    if totals.pph26:
        _ensure_pph21_open(organization, year, month, pph26=True)
    if totals.zakat and settings.zakat_payable_account is None:
        raise ValidationFailed(_("Choose the zakat payable account first (Payroll → Settings)."))
    entry = create_journal_entry(
        organization=organization, date=on, description=f"Gaji karyawan masa {month:02d}/{year}",  # stored records stay Indonesian
        source=JournalEntry.Source.PAYROLL, created_by=created_by, lines=_run_lines(organization, settings, totals),
    )
    run = PayrollRun.objects.create(
        organization=organization, year=year, month=month, gross=totals.pay, employer_contributions=totals.employer,
        employee_contributions=totals.employee, pph21=totals.pph21, pph26=totals.pph26, zakat=totals.zakat, net_pay=totals.net,
        journal_entry=entry, created_by=created_by,
    )
    for slip in slips:
        slip.run = run
    Payslip.objects.bulk_create(slips)
    return run


def _cash(organization, account) -> Account:
    if account is None or account.organization_id != organization.pk or not account.is_cash:
        raise ValidationFailed(_("Choose a valid cash or bank account."))
    return account


@transaction.atomic
def pay_salaries(*, run: PayrollRun, paid_on: date, cash_account, created_by=None) -> PayrollRun:
    run = PayrollRun.objects.select_for_update().select_related("organization").get(pk=run.pk)
    if run.status != PayrollRun.Status.POSTED or run.payment_entry_id:
        raise InvalidStateTransition(_("These salaries have already been paid."))
    organization = run.organization
    settings = settings_for(organization)
    run.payment_entry = create_journal_entry(
        organization=organization, date=paid_on, description=f"Pembayaran gaji masa {run.period}", source=JournalEntry.Source.PAYROLL,
        created_by=created_by, lines=[
            JournalLineInput(settings.salaries_payable_account, debit=run.net_pay),
            JournalLineInput(_cash(organization, cash_account), credit=run.net_pay),
        ],
    )
    run.paid_on = paid_on
    run.save(update_fields=["payment_entry", "paid_on", "updated_at"])
    return run


@transaction.atomic
def pay_bpjs(*, run: PayrollRun, paid_on: date, cash_account, created_by=None) -> PayrollRun:
    run = PayrollRun.objects.select_for_update().select_related("organization").get(pk=run.pk)
    if run.status != PayrollRun.Status.POSTED or run.bpjs_entry_id:
        raise InvalidStateTransition(_("This BPJS has already been paid."))
    total = run.employer_contributions + run.employee_contributions
    if not total:
        raise ValidationFailed(_("This payroll has no BPJS contributions."))
    organization = run.organization
    run.bpjs_entry = create_journal_entry(
        organization=organization, date=paid_on, description=f"Setoran iuran BPJS masa {run.period}", source=JournalEntry.Source.PAYROLL,
        created_by=created_by, lines=[
            JournalLineInput(get_system_account(organization, SystemRole.BPJS_PAYABLE), debit=total),
            JournalLineInput(_cash(organization, cash_account), credit=total),
        ],
    )
    run.bpjs_paid_on = paid_on
    run.save(update_fields=["bpjs_entry", "bpjs_paid_on", "updated_at"])
    return run


@transaction.atomic
def void_payroll(*, run: PayrollRun, on: date, voided_by=None) -> PayrollRun:
    run = PayrollRun.objects.select_for_update().select_related("organization").get(pk=run.pk)
    if run.status != PayrollRun.Status.POSTED:
        raise InvalidStateTransition(_("This payroll has already been voided."))
    if run.payment_entry_id or run.bpjs_entry_id:
        raise InvalidStateTransition(_("Salaries or BPJS of this payroll have been paid; it can't be voided."))
    if PayrollRun.objects.for_organization(run.organization).filter(year=run.year, month__gt=run.month, status=PayrollRun.Status.POSTED).exists():
        raise InvalidStateTransition(_("Later payrolls of the year build on this one. Void them first."))
    _ensure_pph21_open(run.organization, run.year, run.month, pph26=bool(run.pph26))
    reverse_journal_entry(entry=run.journal_entry, date=max(on, run.journal_entry.date), created_by=voided_by,
                          description=f"Pembatalan gaji masa {run.period}")
    run.status = PayrollRun.Status.VOID
    run.save(update_fields=["status", "updated_at"])
    return run


@transaction.atomic
def record_slip_number(*, payslip: Payslip, number: str) -> Payslip:
    payslip = Payslip.objects.select_for_update().get(pk=payslip.pk)
    if payslip.withholding_slip:
        raise InvalidStateTransition(_("This payslip already has its bukti potong number."))
    number = ensure_document_number(number, max_length=50, missing=_("Enter the bukti potong number."))
    payslip.withholding_slip = number
    payslip.save(update_fields=["withholding_slip", "updated_at"])
    return payslip


def _ensure_year_open(employee: Employee, year: int) -> None:
    if Payslip.objects.filter(employee=employee, run__year=year, run__status=PayrollRun.Status.POSTED, tax_scheme=TaxScheme.ANNUAL).exists():
        raise InvalidStateTransition(
            _("%(name)s's PPh 21 for %(year)s has been settled; void that payroll first.") % {"name": employee.name, "year": year}
        )


@transaction.atomic
def add_prior_income(*, employee: Employee, year: int, employer_name: str, net_income: Decimal, pph21: Decimal, first_month: int,
                     last_month: int, employer_npwp: str = "", slip_number: str = "") -> PriorIncome:
    """Record an earlier employer's 1721-A1 of the year (before this employer's last period)."""
    _ensure_year_open(employee, year)
    if not 1 <= first_month <= last_month <= 12:
        raise ValidationFailed(_("Enter the months of that employment."))
    if net_income < 0 or pph21 < 0:
        raise ValidationFailed(_("Amounts can't be negative."))
    return PriorIncome.objects.create(
        organization_id=employee.organization_id, employee=employee, year=year, employer_name=employer_name.strip(),
        employer_npwp=employer_npwp, slip_number=slip_number.strip(), first_month=first_month, last_month=last_month,
        net_income=net_income, pph21=pph21,
    )


@transaction.atomic
def remove_prior_income(*, prior: PriorIncome) -> None:
    _ensure_year_open(prior.employee, prior.year)
    prior.delete()
