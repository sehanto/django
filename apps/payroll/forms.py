from django import forms
from django.utils.translation import gettext_lazy as _

from apps.contacts.validators import npwp_format_validator
from apps.core.forms import BaseForm, BaseModelForm, BusinessDateField, DigitsField, OrganizationFormMixin
from apps.core.money import MONEY_DECIMAL_PLACES, MONEY_MAX_DIGITS
from apps.ledger.domain import AccountCategory
from apps.ledger.models import Account
from apps.ledger.selectors import accounts_for, cash_accounts

from .models import Employee, PayrollSettings, nik_validator
from .services import PayInput


class EmployeeForm(OrganizationFormMixin, BaseModelForm):
    nik = DigitsField(label=_("NIK"), max_length=16, validators=[nik_validator], help_text=Employee._meta.get_field("nik").help_text)
    npwp = DigitsField(label=_("tax ID (NPWP)"), max_length=16, required=False, validators=[npwp_format_validator])

    class Meta:
        model = Employee
        fields = [
            "name", "nik", "npwp", "email", "position", "ptkp_status", "start_date", "end_date", "base_salary",
            "fixed_allowance", "bpjs_employment", "bpjs_health", "is_active",
        ]
        field_classes = {"start_date": BusinessDateField, "end_date": BusinessDateField}


class PayrollSettingsForm(OrganizationFormMixin, BaseModelForm):
    class Meta:
        model = PayrollSettings
        fields = ["salary_expense_account", "benefits_expense_account", "salaries_payable_account", "jkk_rate", "jp_wage_cap", "health_wage_cap"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        accounts = accounts_for(self.organization, active_only=True)
        expenses = accounts.filter(category__in=[AccountCategory.OPERATING_EXPENSE, AccountCategory.COST_OF_SALES])
        self.fields["salary_expense_account"].queryset = expenses
        self.fields["benefits_expense_account"].queryset = expenses
        self.fields["salaries_payable_account"].queryset = accounts.filter(category=AccountCategory.CURRENT_LIABILITY, system_role__isnull=True)


def _money(label, initial):
    return forms.DecimalField(label=label, max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES, min_value=0, initial=initial, required=False)


class PayrollRunForm(OrganizationFormMixin, BaseForm):
    """One row of pay fields per employee working in the month."""

    def __init__(self, *args, employees, **kwargs):
        super().__init__(*args, **kwargs)
        self.employees = list(employees)
        for e in self.employees:
            self.fields[f"base_{e.pk}"] = _money(_("Base salary"), e.base_salary)
            self.fields[f"allowance_{e.pk}"] = _money(_("Allowances"), e.fixed_allowance)
            self.fields[f"bonus_{e.pk}"] = _money(_("Bonus / THR"), 0)

    @property
    def rows(self):
        return [(e, self[f"base_{e.pk}"], self[f"allowance_{e.pk}"], self[f"bonus_{e.pk}"]) for e in self.employees]

    def pays(self) -> list[PayInput]:
        data = self.cleaned_data
        return [
            PayInput(e, data.get(f"base_{e.pk}") or 0, data.get(f"allowance_{e.pk}") or 0, data.get(f"bonus_{e.pk}") or 0)
            for e in self.employees
        ]


class PayForm(OrganizationFormMixin, BaseForm):
    paid_on = BusinessDateField(label=_("Payment date"))
    cash_account = forms.ModelChoiceField(label=_("Paid from"), queryset=Account.objects.none())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["cash_account"].queryset = cash_accounts(self.organization)
