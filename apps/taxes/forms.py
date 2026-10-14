from django import forms
from django.utils.translation import gettext_lazy as _

from apps.core.forms import BaseForm, BaseModelForm, BusinessDateField, OrganizationFormMixin
from apps.core.validators import ntpn_validator
from apps.ledger.models import Account
from apps.ledger.selectors import cash_accounts

from .domain import Month, PayerWithholding
from .models import TaxRate
from .selectors import tax_rate_is_used


class TaxRateForm(OrganizationFormMixin, BaseModelForm):
    LOCKED_WHEN_USED = ("name", "rate", "dpp_numerator", "dpp_denominator", "is_specific_amount", "luxury_rate")

    class Meta:
        model = TaxRate
        fields = ["name", "rate", "dpp_numerator", "dpp_denominator", "is_specific_amount", "luxury_rate", "is_active"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.locked = bool(self.instance.pk) and tax_rate_is_used(self.instance)
        if self.locked:  # the database refuses these changes anyway; say so up front
            for name in self.LOCKED_WHEN_USED:
                self.fields[name].disabled = True
            self.fields["is_active"].help_text = _(
                "This rate is used on issued invoices, so only its active status can change. "
                "To charge a different rate, deactivate it and add a new one."
            )


class PphFinalPaymentForm(OrganizationFormMixin, BaseForm):
    month = forms.TypedChoiceField(widget=forms.HiddenInput, choices=Month.choices, coerce=lambda value: Month(int(value)))
    paid_on = BusinessDateField(label=_("Payment date"))
    cash_account = forms.ModelChoiceField(label=_("Paid from"), queryset=Account.objects.none())
    ntpn = forms.CharField(label=_("NTPN"), max_length=32, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["cash_account"].queryset = cash_accounts(self.organization)

    def clean_ntpn(self):
        ntpn = "".join(ch for ch in self.cleaned_data["ntpn"] if ch.isalnum()).upper()  # drop spaces/dashes
        if ntpn:
            ntpn_validator(ntpn)
        return ntpn


class WithholdingDepositForm(OrganizationFormMixin, BaseForm):
    kind = forms.ChoiceField(widget=forms.HiddenInput, choices=PayerWithholding.choices)
    month = forms.TypedChoiceField(widget=forms.HiddenInput, choices=Month.choices, coerce=lambda value: Month(int(value)))
    paid_on = BusinessDateField(label=_("Payment date"))
    cash_account = forms.ModelChoiceField(label=_("Paid from"), queryset=Account.objects.none())
    ntpn = forms.CharField(label=_("NTPN"), max_length=32, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["cash_account"].queryset = cash_accounts(self.organization)

    def clean_ntpn(self):
        ntpn = "".join(ch for ch in self.cleaned_data["ntpn"] if ch.isalnum()).upper()
        if ntpn:
            ntpn_validator(ntpn)
        return ntpn


class TaxObjectForm(BaseForm):
    """The tax object code of a supplier payment recorded without one."""

    def __init__(self, *args, **kwargs):
        from apps.invoicing.forms import tax_object_field  # invoicing's payment form uses the same list

        super().__init__(*args, auto_id=False, **kwargs)
        self.fields["code"] = tax_object_field(required=True)
        self.fields["code"].widget.attrs.update({"class": "form-select form-select-sm", "aria-label": self.fields["code"].label})
