from django import forms
from django.utils.translation import gettext_lazy as _

from apps.core.forms import BaseForm, BaseModelForm, BusinessDateField, OrganizationFormMixin
from apps.core.validators import ntpn_validator
from apps.ledger.models import Account
from apps.ledger.selectors import cash_accounts

from .domain import Month
from .models import TaxRate


class TaxRateForm(OrganizationFormMixin, BaseModelForm):
    class Meta:
        model = TaxRate
        fields = ["name", "rate", "dpp_numerator", "dpp_denominator", "is_active"]


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
