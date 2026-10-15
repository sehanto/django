from datetime import date

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.contacts.countries import INDONESIA
from apps.contacts.models import validate_country_code
from apps.contacts.validators import npwp_format_validator
from apps.core.models import TenantOwnedModel, TimeStampedModel, money_field
from apps.core.validators import document_number_validator, identifier_validator
from apps.regions.models import Address

from .domain import (
    BpjsRates,
    EmployeeKind,
    PayBasis,
    PtkpStatus,
    TaxScheme,
    TerCategory,
)

nik_validator = RegexValidator(r"^\d{16}$", _("A NIK has 16 digits."))


class PayrollSettings(TimeStampedModel):
    """An organization's payroll accounts and BPJS rates."""

    organization = models.OneToOneField("tenants.Organization", on_delete=models.CASCADE, related_name="payroll_settings")
    salary_expense_account = models.ForeignKey("ledger.Account", on_delete=models.PROTECT, related_name="+", verbose_name=_("salary expense account"))
    benefits_expense_account = models.ForeignKey(
        "ledger.Account", on_delete=models.PROTECT, related_name="+", verbose_name=_("BPJS expense account"),
        help_text=_("The employer's share of BPJS contributions."),
    )
    salaries_payable_account = models.ForeignKey("ledger.Account", on_delete=models.PROTECT, related_name="+", verbose_name=_("salaries payable account"))
    jkk_rate = models.DecimalField(
        _("JKK rate (%)"), max_digits=4, decimal_places=2, default=BpjsRates.jkk,
        validators=[MinValueValidator(0), MaxValueValidator(10)], help_text=_("Work accident insurance, by risk group: 0.24 to 1.74."),
    )
    zakat_payable_account = models.ForeignKey(
        "ledger.Account", null=True, blank=True, on_delete=models.PROTECT, related_name="+", verbose_name=_("zakat payable account"),
        help_text=_("Zakat or religious donations deducted from pay, until you pass them on."),
    )
    jp_wage_cap = money_field(_("JP wage cap"), default=BpjsRates.jp_wage_cap, help_text=_("Set by BPJS Ketenagakerjaan every year."))
    health_wage_cap = money_field(_("BPJS Kesehatan wage cap"), default=BpjsRates.health_wage_cap)

    class Meta:
        verbose_name = _("payroll settings")
        verbose_name_plural = _("payroll settings")

    def rates(self) -> BpjsRates:
        return BpjsRates(jkk=self.jkk_rate, jp_wage_cap=self.jp_wage_cap, health_wage_cap=self.health_wage_cap)


class Employee(TenantOwnedModel, Address):
    PtkpStatus = PtkpStatus
    Kind = EmployeeKind
    PayBasis = PayBasis

    class Gender(models.TextChoices):
        UNSPECIFIED = "", _("Not specified")
        MALE = "M", _("Male")
        FEMALE = "F", _("Female")

    name = models.CharField(_("name"), max_length=200)
    kind = models.CharField(_("employment"), max_length=15, choices=EmployeeKind.choices, default=EmployeeKind.PERMANENT)
    pay_basis = models.CharField(
        _("paid"), max_length=10, choices=PayBasis.choices, default=PayBasis.MONTHLY,
        help_text=_("Non-permanent workers paid by the day are taxed at the daily rates."),
    )
    nationality = models.CharField(_("nationality"), max_length=3, default=INDONESIA, validators=[validate_country_code],
                                   help_text=_("Country code, e.g. IDN, AUS, JPN."))
    nik = models.CharField(_("NIK"), max_length=16, blank=True, validators=[nik_validator],
                           help_text=_("16 digits; also the NPWP of an Indonesian citizen."))
    passport_number = models.CharField(_("passport no."), max_length=30, blank=True, validators=[identifier_validator], help_text=_("For foreign employees."))
    npwp = models.CharField(_("tax ID (NPWP)"), max_length=16, blank=True, validators=[npwp_format_validator])
    is_resident = models.BooleanField(
        _("Indonesian tax resident"), default=True,
        help_text=_("Foreigners in Indonesia for less than 183 days in 12 months are not: their pay bears 20% PPh 26 instead of PPh 21."),
    )
    resident_since = models.DateField(
        _("tax resident since"), null=True, blank=True,
        help_text=_("For someone who moved to Indonesia: in that year their income is annualised at year end."),
    )
    leaves_indonesia = models.BooleanField(
        _("leaves Indonesia for good at the end date"), default=False,
        help_text=_("Their last month's PPh 21 is then computed on annualised income."),
    )
    tax_borne_by_employer = models.BooleanField(
        _("the employer pays the PPh 21"), default=False,
        help_text=_("A tax allowance equal to the tax is added to the pay (gross-up), so the take-home pay is unchanged."),
    )
    zakat = money_field(
        _("zakat per month"), validators=[MinValueValidator(0)],
        help_text=_("Zakat or a compulsory religious donation deducted from pay for an official body; it lowers the year's taxable income."),
    )
    treaty_rate = models.DecimalField(
        _("tax treaty rate for PPh 26 (%)"), max_digits=4, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(20)],
        help_text=_("Only with the employee's certificate of residence (DGT form); empty for the standard 20%."),
    )
    treaty_document = models.CharField(_("certificate of residence no."), max_length=50, blank=True, validators=[document_number_validator])
    foreign_tin = models.CharField(
        _("foreign tax ID"), max_length=30, blank=True, validators=[identifier_validator], help_text=_("A non-resident's tax ID in their country, for the PPh 26 bukti potong."),
    )
    gender = models.CharField(_("gender"), max_length=1, choices=Gender.choices, blank=True, default=Gender.UNSPECIFIED)
    email = models.EmailField(_("email"), blank=True)
    position = models.CharField(_("position"), max_length=100, blank=True)
    ptkp_status = models.CharField(
        _("PTKP status"), max_length=4, choices=PtkpStatus.choices, default=PtkpStatus.TK0,
        help_text=_("Marital status and dependents on 1 January of the year."),
    )
    start_date = models.DateField(_("start date"))
    end_date = models.DateField(_("end date"), null=True, blank=True, help_text=_("Their last month is then the last PPh 21 period of the year."))
    base_salary = money_field(_("base salary"), validators=[MinValueValidator(0)], help_text=_("A month's; for daily workers, a day's wage."))
    fixed_allowance = money_field(_("fixed allowances"), validators=[MinValueValidator(0)])
    bpjs_employment = models.BooleanField(_("BPJS Ketenagakerjaan"), default=True, help_text=_("JKK, JKM, JHT and JP."))
    bpjs_health = models.BooleanField(_("BPJS Kesehatan"), default=True)
    is_active = models.BooleanField(
        _("active"), default=True,
        help_text=_("Untick when nothing more is paid to them; a former employee who is still active can be paid severance in instalments."),
    )

    class Meta:
        ordering = ["name"]
        verbose_name = _("employee")
        verbose_name_plural = _("employees")

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        errors = {}
        if self.nationality == INDONESIA and not self.nik:
            errors["nik"] = _("Enter the NIK of an Indonesian citizen.")
        if self.nationality != INDONESIA and not self.passport_number and not self.nik:
            errors["passport_number"] = _("Enter the passport number of a foreign employee.")
        if self.end_date and self.start_date and self.end_date < self.start_date:
            errors["end_date"] = _("The end date can't be before the start date.")
        if self.leaves_indonesia and not self.end_date:
            errors["end_date"] = _("Enter the date they stop working here.")
        if self.treaty_rate is not None and not self.treaty_document:
            errors["treaty_document"] = _("A treaty rate needs the certificate of residence.")
        if errors:
            raise ValidationError(errors)

    @property
    def is_citizen(self) -> bool:
        return self.nationality == INDONESIA

    @property
    def tin(self) -> str:
        """The tax ID on bukti potong: the NPWP, or the NIK of an Indonesian citizen."""
        return self.npwp or (self.nik if self.is_citizen else "")

    def left_before(self, year: int, month: int) -> bool:
        """They left before the month: only severance (paid in instalments) can still be paid."""
        return self.end_date is not None and self.end_date < date(year, month, 1)

    @property
    def is_daily(self) -> bool:
        return self.kind == EmployeeKind.NON_PERMANENT and self.pay_basis == PayBasis.DAILY

    @property
    def id_label(self) -> str:
        return self.nik if self.nik else self.passport_number


class PayrollRun(TenantOwnedModel):
    """One month's payroll, posted with its journal entry. Final once posted (payroll 0002):
    only paying the salaries and BPJS (once each) and voiding (after the entry is reversed)."""

    class Status(models.TextChoices):
        POSTED = "posted", _("Recorded")
        VOID = "void", _("Void")

    year = models.PositiveSmallIntegerField(_("year"))
    month = models.PositiveSmallIntegerField(_("month"), validators=[MinValueValidator(1), MaxValueValidator(12)])
    status = models.CharField(_("status"), max_length=10, choices=Status.choices, default=Status.POSTED)
    gross = money_field(_("salaries and allowances"))
    employer_contributions = money_field(_("employer BPJS"))
    employee_contributions = money_field(_("employee BPJS"))
    pph21 = money_field(_("PPh 21"))
    pph26 = money_field(_("PPh 26"))
    zakat = money_field(_("zakat deducted"))
    net_pay = money_field(_("net pay"))
    journal_entry = models.OneToOneField("ledger.JournalEntry", on_delete=models.PROTECT, related_name="+")
    paid_on = models.DateField(_("salaries paid on"), null=True, blank=True)
    payment_entry = models.OneToOneField("ledger.JournalEntry", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    bpjs_paid_on = models.DateField(_("BPJS paid on"), null=True, blank=True)
    bpjs_entry = models.OneToOneField("ledger.JournalEntry", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-year", "-month"]
        verbose_name = _("payroll run")
        verbose_name_plural = _("payroll runs")
        constraints = [
            models.UniqueConstraint(fields=["organization", "year", "month"], condition=models.Q(status="posted"), name="unique_payroll_period"),
        ]

    def __str__(self):
        return f"Gaji {self.month:02d}/{self.year}"

    @property
    def period(self) -> str:
        return f"{self.month:02d}/{self.year}"

    @property
    def income_tax(self):
        """PPh 21 and PPh 26 withheld."""
        return self.pph21 + self.pph26


class Payslip(TimeStampedModel):
    """An employee's pay in a run, with the PPh 21 withheld (one bukti potong)."""

    run = models.ForeignKey(PayrollRun, on_delete=models.CASCADE, related_name="payslips")
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="payslips")
    ptkp_status = models.CharField(_("PTKP status"), max_length=4, choices=PtkpStatus.choices)
    base_salary = money_field(_("base salary"))
    allowances = money_field(_("allowances"))
    bonus = money_field(_("bonus / THR"))
    tax_allowance = money_field(_("tax allowance"))
    benefits_in_kind = money_field(_("benefits in kind"), help_text=_("Taxable, but not paid in cash."))
    severance = money_field(_("severance pay"))
    severance_tax = money_field(_("PPh 21 on severance"))
    severance_final = models.BooleanField(
        _("severance taxed as final"), default=True,
        help_text=_("Final within two calendar years of the first payment; from the third year the Art. 17 rates (PP 68/2009)."),
    )
    zakat = money_field(_("zakat deducted"))
    jkk = money_field("JKK")
    jkm = money_field("JKM")
    jht_employer = money_field(_("JHT (employer)"))
    jp_employer = money_field(_("JP (employer)"))
    health_employer = money_field(_("BPJS Kesehatan (employer)"))
    jht_employee = money_field(_("JHT (employee)"))
    jp_employee = money_field(_("JP (employee)"))
    health_employee = money_field(_("BPJS Kesehatan (employee)"))
    taxable_gross = money_field(_("gross income for PPh 21"))
    ter_category = models.CharField(_("TER category"), max_length=1, choices=TerCategory.choices)
    ter_rate = models.DecimalField(_("TER rate (%)"), max_digits=5, decimal_places=2, null=True, blank=True)
    is_final_period = models.BooleanField(_("last period of the year"), default=False)
    tax_scheme = models.CharField(_("tax computed as"), max_length=15, choices=TaxScheme.choices, default=TaxScheme.TER_MONTHLY)
    days_worked = models.PositiveSmallIntegerField(_("days worked"), null=True, blank=True)
    annualized = models.BooleanField(_("annualised"), default=False)
    pph26_rate = models.DecimalField(_("PPh 26 rate (%)"), max_digits=4, decimal_places=2, null=True, blank=True)
    # xgettext:no-python-format
    without_tin = models.BooleanField(_("without NPWP (20% higher)"), default=False)
    pph21 = money_field(_("PPh 21"), help_text=_("PPh 26 when the scheme says so."))
    net_pay = money_field(_("net pay"))
    withholding_slip = models.CharField(_("bukti potong no."), max_length=50, blank=True, validators=[document_number_validator])

    class Meta:
        ordering = ["employee__name"]
        verbose_name = _("payslip")
        verbose_name_plural = _("payslips")

    def __str__(self):
        return f"{self.employee} {self.run.period}"

    @property
    def is_severance_only(self) -> bool:
        return self.tax_scheme == TaxScheme.SEVERANCE_ONLY

    @property
    def is_pph26(self) -> bool:
        return self.tax_scheme == TaxScheme.PPH_26

    @property
    def pay(self):
        """What the employee is paid in cash before deductions."""
        return self.base_salary + self.allowances + self.bonus + self.tax_allowance + self.severance

    @property
    def tax_withheld(self):
        return self.pph21 + self.severance_tax

    @property
    def employee_contributions(self):
        return self.jht_employee + self.jp_employee + self.health_employee

    @property
    def employer_contributions(self):
        return self.jkk + self.jkm + self.jht_employer + self.jp_employer + self.health_employer


class PriorIncome(TenantOwnedModel):
    """An earlier employer's 1721-A1 for the year, handed in by an employee who joined mid-year:
    its net income and PPh 21 count in this employer's year-end calculation (lines 14 and 19).
    Fixed once the year's last payroll of the employee is posted (``services``)."""

    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="prior_incomes")
    year = models.PositiveSmallIntegerField(_("year"))
    employer_name = models.CharField(_("previous employer"), max_length=200)
    employer_npwp = models.CharField(_("previous employer's NPWP"), max_length=16, blank=True, validators=[npwp_format_validator])
    slip_number = models.CharField(_("their 1721-A1 no."), max_length=50, blank=True, validators=[document_number_validator])
    first_month = models.PositiveSmallIntegerField(_("from month"), validators=[MinValueValidator(1), MaxValueValidator(12)])
    last_month = models.PositiveSmallIntegerField(_("to month"), validators=[MinValueValidator(1), MaxValueValidator(12)])
    net_income = money_field(_("net income"), validators=[MinValueValidator(0)], help_text=_("Line 13 of their 1721-A1."))
    pph21 = money_field(_("PPh 21 withheld"), validators=[MinValueValidator(0)], help_text=_("Line 21 of their 1721-A1."))

    class Meta:
        ordering = ["year", "first_month"]
        verbose_name = _("income from a previous employer")
        verbose_name_plural = _("income from previous employers")

    def __str__(self):
        return f"{self.employer_name} {self.year}"
