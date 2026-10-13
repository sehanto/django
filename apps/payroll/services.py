"""Payroll use cases: a month's run posts salaries, BPJS and PPh 21; paying the salaries and
the BPJS are separate entries; the PPh 21 is deposited with the other withheld taxes
(``taxes.WithholdingDeposit``, kind PPh 21)."""

import calendar
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.db import transaction
from django.utils.translation import gettext as _

from apps.core.exceptions import InvalidStateTransition, ValidationFailed
from apps.core.money import ZERO
from apps.core.validators import ensure_business_date
from apps.ledger.domain import AccountCategory, SystemRole
from apps.ledger.models import Account, JournalEntry
from apps.ledger.services import JournalLineInput, create_journal_entry, get_system_account, reverse_journal_entry

from .domain import TER_CATEGORY, PtkpStatus, annual_pph21, contributions, monthly_pph21
from .models import Employee, PayrollRun, PayrollSettings, Payslip

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
    settings, _created = PayrollSettings.objects.update_or_create(organization=organization, defaults=fields)
    return settings


def month_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def is_final_period(employee: Employee, year: int, month: int) -> bool:
    """December, or the month the employee leaves: PPh 21 is then computed for the year."""
    end = employee.end_date
    return month == 12 or (end is not None and (end.year, end.month) == (year, month))


@dataclass(frozen=True)
class PayInput:
    employee: Employee
    base_salary: Decimal
    allowances: Decimal = ZERO
    bonus: Decimal = ZERO  # bonus, THR and other irregular pay (no BPJS on it)


def compute_payslip(settings: PayrollSettings, employee: Employee, year: int, month: int, pay: PayInput) -> Payslip:
    """The employee's payslip for the month (unsaved): BPJS, PPh 21 and net pay."""
    if min(pay.base_salary, pay.allowances, pay.bonus) < 0:
        raise ValidationFailed(_("Pay amounts can't be negative."))
    status = PtkpStatus(employee.ptkp_status)
    wage = pay.base_salary + pay.allowances
    c = contributions(wage, settings.rates(), employment=employee.bpjs_employment, health=employee.bpjs_health)
    gross = wage + pay.bonus + c.taxable_premiums
    slip = Payslip(
        employee=employee, ptkp_status=status, base_salary=pay.base_salary, allowances=pay.allowances, bonus=pay.bonus,
        jkk=c.jkk, jkm=c.jkm, jht_employer=c.jht_employer, jp_employer=c.jp_employer, health_employer=c.health_employer,
        jht_employee=c.jht_employee, jp_employee=c.jp_employee, health_employee=c.health_employee,
        taxable_gross=gross, ter_category=TER_CATEGORY[status], is_final_period=is_final_period(employee, year, month),
    )
    if slip.is_final_period:
        earlier = Payslip.objects.filter(
            employee=employee, run__year=year, run__month__lt=month, run__status=PayrollRun.Status.POSTED,
        )
        months = earlier.values("run__month").distinct().count() + 1
        earlier_gross = sum((p.taxable_gross for p in earlier), ZERO)
        earlier_contributions = sum((p.jht_employee + p.jp_employee for p in earlier), ZERO)
        withheld = sum((p.pph21 for p in earlier), ZERO)
        annual = annual_pph21(status, earlier_gross + gross, earlier_contributions + c.jht_employee + c.jp_employee, months)
        slip.ter_rate, slip.pph21 = None, annual.tax - withheld  # negative: a refund to the employee
    else:
        slip.ter_rate, slip.pph21 = monthly_pph21(status, gross)
    slip.net_pay = wage + pay.bonus - c.employee - slip.pph21
    return slip


def _ensure_pph21_open(organization, year: int, month: int) -> None:
    from apps.taxes.models import WithholdingDeposit  # taxes reads payroll lazily too

    if WithholdingDeposit.objects.for_organization(organization).filter(
        kind=WithholdingDeposit.Kind.PPH_21, year=year, month=month, status=WithholdingDeposit.Status.POSTED,
    ).exists():
        raise InvalidStateTransition(
            _("The PPh 21 for %(period)s has already been deposited. Cancel that deposit first.") % {"period": f"{month:02d}/{year}"}
        )


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
    pays = [p for p in pays if p.base_salary or p.allowances or p.bonus]
    if not pays:
        raise ValidationFailed(_("Enter the pay of at least one employee."))
    if len({p.employee.pk for p in pays}) != len(pays):
        raise ValidationFailed(_("Each employee is paid once per run."))
    settings = settings_for(organization)
    slips = []
    for pay in pays:
        employee = pay.employee
        if employee.organization_id != organization.pk:
            raise ValidationFailed(_("Employee not found."))
        if employee.start_date > on or (employee.end_date and employee.end_date < date(year, month, 1)):
            raise ValidationFailed(_("%(name)s doesn't work here in %(period)s.") % {"name": employee.name, "period": f"{month:02d}/{year}"})
        slips.append(compute_payslip(settings, employee, year, month, pay))

    pay_total = sum((s.pay for s in slips), ZERO)
    employer = sum((s.employer_contributions for s in slips), ZERO)
    employee_part = sum((s.employee_contributions for s in slips), ZERO)
    pph21 = sum((s.pph21 for s in slips), ZERO)
    net = sum((s.net_pay for s in slips), ZERO)
    pph21_account = get_system_account(organization, SystemRole.PPH_21_PAYABLE)
    lines = [
        JournalLineInput(settings.salary_expense_account, debit=pay_total),
        JournalLineInput(settings.benefits_expense_account, debit=employer),
        JournalLineInput(pph21_account, credit=pph21) if pph21 >= 0 else JournalLineInput(pph21_account, debit=-pph21),
        JournalLineInput(get_system_account(organization, SystemRole.BPJS_PAYABLE), credit=employer + employee_part),
        JournalLineInput(settings.salaries_payable_account, credit=net),
    ]
    entry = create_journal_entry(
        organization=organization, date=on, description=f"Gaji karyawan masa {month:02d}/{year}",  # stored records stay Indonesian
        source=JournalEntry.Source.PAYROLL, created_by=created_by, lines=[line for line in lines if line.debit or line.credit],
    )
    run = PayrollRun.objects.create(
        organization=organization, year=year, month=month, gross=pay_total, employer_contributions=employer,
        employee_contributions=employee_part, pph21=pph21, net_pay=net, journal_entry=entry, created_by=created_by,
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
    _ensure_pph21_open(run.organization, run.year, run.month)
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
    number = number.strip()
    if not number or len(number) > 50:
        raise ValidationFailed(_("Enter the bukti potong number."))
    payslip.withholding_slip = number
    payslip.save(update_fields=["withholding_slip", "updated_at"])
    return payslip
