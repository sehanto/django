from django import forms

from apps.core.forms import DateInput, OrganizationFormMixin
from apps.core.money import ZERO

from .models import Account, JournalEntry
from .selectors import accounts_for
from .services import JournalLineInput


class AccountForm(OrganizationFormMixin, forms.ModelForm):
    class Meta:
        model = Account
        fields = ["code", "name", "category", "is_contra", "description", "is_active"]


class JournalEntryForm(OrganizationFormMixin, forms.ModelForm):
    class Meta:
        model = JournalEntry
        fields = ["date", "description", "reference"]
        widgets = {"date": DateInput()}


class JournalLineForm(OrganizationFormMixin, forms.Form):
    account = forms.ModelChoiceField(label="Akun", queryset=Account.objects.none())
    description = forms.CharField(label="Keterangan", max_length=255, required=False)
    debit = forms.DecimalField(label="Debit", max_digits=18, decimal_places=2, min_value=0, required=False)
    credit = forms.DecimalField(label="Kredit", max_digits=18, decimal_places=2, min_value=0, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["account"].queryset = accounts_for(self.organization, active_only=True)

    def to_input(self) -> JournalLineInput:
        data = self.cleaned_data
        return JournalLineInput(
            account=data["account"],
            debit=data.get("debit") or ZERO,
            credit=data.get("credit") or ZERO,
            description=data.get("description", ""),
        )


JournalLineFormSet = forms.formset_factory(JournalLineForm, extra=0, min_num=2, validate_min=True)


def line_inputs(formset) -> list[JournalLineInput]:
    return [f.to_input() for f in formset.forms if f.has_changed() and not f.cleaned_data.get("DELETE")]


class ReverseForm(forms.Form):
    date = forms.DateField(label="Tanggal pembalikan", widget=DateInput())
