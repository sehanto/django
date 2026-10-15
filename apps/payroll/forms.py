from django import forms
from django.utils.translation import gettext_lazy as _

from apps.contacts.validators import npwp_format_validator
from apps.core.forms import BaseForm, BaseModelForm, BusinessDateField, DigitsField, OrganizationFormMixin
from apps.regions.forms import RegionFieldsMixin
from apps.core.money import MONEY_DECIMAL_PLACES, MONEY_MAX_DIGITS
from apps.ledger.domain import AccountCategory
from apps.ledger.models import Account
from apps.ledger.selectors import accounts_for, cash_accounts

from .models import Employee, PayrollSettings, PriorIncome, nik_validator
from .services import PayInput


class EmployeeForm(RegionFieldsMixin, OrganizationFormMixin, BaseModelForm):
    nik = DigitsField(label=_("NIK"), max_length=16, required=False, validators=[nik_validator], help_text=Employee._meta.get_field("nik").help_text)
    npwp = DigitsField(label=_("tax ID (NPWP)"), max_length=16, required=False, validators=[npwp_format_validator])

    class Meta:
        model = Employee
        fields = [
            "name", "kind", "pay_basis", "nationality", "nik", "passport_number", "npwp", "is_resident", "resident_since",
            "treaty_rate", "treaty_document", "foreign_tin", "gender", "address", "village", "email", "position", "ptkp_status", "start_date", "end_date",
            "leaves_indonesia", "base_salary", "fixed_allowance", "bpjs_employment", "bpjs_health", "tax_borne_by_employer", "zakat",
            "is_active",
        ]
        field_classes = {"start_date": BusinessDateField, "end_date": BusinessDateField, "resident_since": BusinessDateField}

    def clean_nationality(self):
        return (self.cleaned_data.get("nationality") or "").upper()

    def is_domestic(self, cleaned_data) -> bool:
        """A non-resident may live abroad: their address is then written in full."""
        return bool(cleaned_data.get("is_resident", True))


class PayrollSettingsForm(OrganizationFormMixin, BaseModelForm):
    class Meta:
        model = PayrollSettings
        fields = [
            "salary_expense_account", "benefits_expense_account", "salaries_payable_account", "zakat_payable_account", "jkk_rate",
            "jp_wage_cap", "health_wage_cap",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        accounts = accounts_for(self.organization, active_only=True)
        expenses = accounts.filter(category__in=[AccountCategory.OPERATING_EXPENSE, AccountCategory.COST_OF_SALES])
        self.fields["salary_expense_account"].queryset = expenses
        self.fields["benefits_expense_account"].queryset = expenses
        liabilities = accounts.filter(category=AccountCategory.CURRENT_LIABILITY, system_role__isnull=True)
        self.fields["salaries_payable_account"].queryset = liabilities
        self.fields["zakat_payable_account"].queryset = liabilities


CELL = {"class": "form-control form-control-sm text-end cell-amount"}


def _money(label, initial):
    return forms.DecimalField(
        label=label, max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES, min_value=0, initial=initial, required=False,
        widget=forms.NumberInput(attrs={**CELL, "step": "any"}),
    )


class PayrollRunForm(OrganizationFormMixin, BaseForm):
    """One row of pay fields per employee working in the month."""

    def __init__(self, *args, employees, year: int, month: int, **kwargs):
        super().__init__(*args, **kwargs)
        self.employees = list(employees)
        self.year, self.month = year, month
        for e in self.employees:
            if e.left_before(year, month):  # severance in instalments only
                self.fields[f"severance_{e.pk}"] = _money(_("Severance pay"), 0)
                continue
            # Daily workers: the month's wages, or the days worked at their daily wage.
            self.fields[f"base_{e.pk}"] = _money(_("Base salary"), None if e.is_daily else e.base_salary)
            self.fields[f"allowance_{e.pk}"] = _money(_("Allowances"), e.fixed_allowance)
            self.fields[f"bonus_{e.pk}"] = _money(_("Bonus / THR"), 0)
            self.fields[f"natura_{e.pk}"] = _money(_("Benefits in kind"), 0)
            self.fields[f"severance_{e.pk}"] = _money(_("Severance pay"), 0)
            if e.is_daily:
                self.fields[f"days_{e.pk}"] = forms.IntegerField(
                    label=_("Days worked"), min_value=0, max_value=31, required=False,
                    widget=forms.NumberInput(attrs={**CELL, "class": "form-control form-control-sm text-end cell-days"}),
                )
            for name, field in self.fields.items():  # a grid: each input says whose pay it is
                if name.endswith(f"_{e.pk}"):
                    field.widget.attrs["aria-label"] = f"{field.label}: {e.name}"

    @property
    def rows(self):
        rows = []
        for e in self.employees:
            if e.left_before(self.year, self.month):
                rows.append((e, None, None, None, None, self[f"severance_{e.pk}"], None))
                continue
            rows.append((e, self[f"base_{e.pk}"], self[f"allowance_{e.pk}"], self[f"bonus_{e.pk}"], self[f"natura_{e.pk}"],
                         self[f"severance_{e.pk}"], self[f"days_{e.pk}"] if e.is_daily else None))
        return rows

    def pays(self) -> list[PayInput]:
        data, pays = self.cleaned_data, []
        for e in self.employees:
            base, days = data.get(f"base_{e.pk}"), data.get(f"days_{e.pk}") if e.is_daily else None
            if base is None and days:
                base = e.base_salary * days
            pays.append(PayInput(
                e, base or 0, data.get(f"allowance_{e.pk}") or 0, data.get(f"bonus_{e.pk}") or 0, days,
                benefits_in_kind=data.get(f"natura_{e.pk}") or 0, severance=data.get(f"severance_{e.pk}") or 0,
            ))
        return pays


class PayForm(OrganizationFormMixin, BaseForm):
    paid_on = BusinessDateField(label=_("Payment date"))
    cash_account = forms.ModelChoiceField(label=_("Paid from"), queryset=Account.objects.none())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["cash_account"].queryset = cash_accounts(self.organization)


class PriorIncomeForm(BaseModelForm):
    employer_npwp = DigitsField(label=_("previous employer's NPWP"), max_length=16, required=False, validators=[npwp_format_validator])

    class Meta:
        model = PriorIncome
        fields = ["year", "employer_name", "employer_npwp", "slip_number", "first_month", "last_month", "net_income", "pph21"]
