from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.contacts.validators import npwp_format_validator
from apps.core.models import TenantOwnedModel, TimeStampedModel, money_field

from .domain import BpjsRates, PtkpStatus, TerCategory

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
    jp_wage_cap = money_field(_("JP wage cap"), default=BpjsRates.jp_wage_cap, help_text=_("Set by BPJS Ketenagakerjaan every year."))
    health_wage_cap = money_field(_("BPJS Kesehatan wage cap"), default=BpjsRates.health_wage_cap)

    class Meta:
        verbose_name = _("payroll settings")
        verbose_name_plural = _("payroll settings")

    def rates(self) -> BpjsRates:
        return BpjsRates(jkk=self.jkk_rate, jp_wage_cap=self.jp_wage_cap, health_wage_cap=self.health_wage_cap)


class Employee(TenantOwnedModel):
    PtkpStatus = PtkpStatus

    name = models.CharField(_("name"), max_length=200)
    nik = models.CharField(_("NIK"), max_length=16, validators=[nik_validator], help_text=_("16 digits; also the NPWP for PPh 21."))
    npwp = models.CharField(_("tax ID (NPWP)"), max_length=16, blank=True, validators=[npwp_format_validator])
    email = models.EmailField(_("email"), blank=True)
    position = models.CharField(_("position"), max_length=100, blank=True)
    ptkp_status = models.CharField(
        _("PTKP status"), max_length=4, choices=PtkpStatus.choices, default=PtkpStatus.TK0,
        help_text=_("Marital status and dependents on 1 January of the year."),
    )
    start_date = models.DateField(_("start date"))
    end_date = models.DateField(_("end date"), null=True, blank=True, help_text=_("Their last month is then the last PPh 21 period of the year."))
    base_salary = money_field(_("base salary"), validators=[MinValueValidator(0)])
    fixed_allowance = money_field(_("fixed allowances"), validators=[MinValueValidator(0)])
    bpjs_employment = models.BooleanField(_("BPJS Ketenagakerjaan"), default=True, help_text=_("JKK, JKM, JHT and JP."))
    bpjs_health = models.BooleanField(_("BPJS Kesehatan"), default=True)
    is_active = models.BooleanField(_("active"), default=True)

    class Meta:
        ordering = ["name"]
        verbose_name = _("employee")
        verbose_name_plural = _("employees")

    def __str__(self):
        return self.name


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


class Payslip(TimeStampedModel):
    """An employee's pay in a run, with the PPh 21 withheld (one bukti potong)."""

    run = models.ForeignKey(PayrollRun, on_delete=models.CASCADE, related_name="payslips")
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="payslips")
    ptkp_status = models.CharField(_("PTKP status"), max_length=4, choices=PtkpStatus.choices)
    base_salary = money_field(_("base salary"))
    allowances = money_field(_("allowances"))
    bonus = money_field(_("bonus / THR"))
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
    pph21 = money_field(_("PPh 21"))
    net_pay = money_field(_("net pay"))
    withholding_slip = models.CharField(_("bukti potong no."), max_length=50, blank=True)

    class Meta:
        ordering = ["employee__name"]
        verbose_name = _("payslip")
        verbose_name_plural = _("payslips")

    def __str__(self):
        return f"{self.employee} {self.run.period}"

    @property
    def pay(self):
        return self.base_salary + self.allowances + self.bonus

    @property
    def employee_contributions(self):
        return self.jht_employee + self.jp_employee + self.health_employee

    @property
    def employer_contributions(self):
        return self.jkk + self.jkm + self.jht_employer + self.jp_employer + self.health_employer
