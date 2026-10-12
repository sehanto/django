from django import forms

from apps.core.forms import DateInput, OrganizationFormMixin
from apps.ledger.models import Account
from apps.ledger.selectors import cash_accounts

from .models import TaxRate


class TaxRateForm(OrganizationFormMixin, forms.ModelForm):
    class Meta:
        model = TaxRate
        fields = ["name", "rate", "dpp_numerator", "dpp_denominator", "is_active"]

    def clean_dpp_denominator(self):
        value = self.cleaned_data["dpp_denominator"]
        if value == 0:
            raise forms.ValidationError("Penyebut DPP tidak boleh nol.")
        return value


class PphFinalPaymentForm(OrganizationFormMixin, forms.Form):
    month = forms.IntegerField(widget=forms.HiddenInput, min_value=1, max_value=12)
    paid_on = forms.DateField(label="Tanggal setor", widget=DateInput())
    cash_account = forms.ModelChoiceField(label="Dibayar dari", queryset=Account.objects.none())
    ntpn = forms.CharField(label="NTPN", max_length=32, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["cash_account"].queryset = cash_accounts(self.organization)
