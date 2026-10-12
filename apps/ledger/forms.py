from django import forms
from django.utils.translation import gettext_lazy as _

from apps.core.forms import BaseForm, BaseModelForm, BusinessDateField, OrganizationFormMixin
from apps.core.money import ZERO

from .models import Account, JournalEntry
from .selectors import accounts_for
from .services import JournalLineInput


class AccountForm(OrganizationFormMixin, BaseModelForm):
    class Meta:
        model = Account
        fields = ["code", "name", "category", "is_contra", "description", "is_active"]


class JournalEntryForm(OrganizationFormMixin, BaseModelForm):
    class Meta:
        model = JournalEntry
        fields = ["date", "description", "reference"]
        field_classes = {"date": BusinessDateField}


class JournalLineForm(OrganizationFormMixin, BaseForm):
    account = forms.ModelChoiceField(label=_("Account"), queryset=Account.objects.none())
    description = forms.CharField(label=_("Description"), max_length=255, required=False)
    debit = forms.DecimalField(label=_("Debit"), max_digits=18, decimal_places=2, min_value=0, required=False)
    credit = forms.DecimalField(label=_("Credit"), max_digits=18, decimal_places=2, min_value=0, required=False)

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


MAX_JOURNAL_LINES = 100
JournalLineFormSet = forms.formset_factory(
    JournalLineForm, extra=0, min_num=2, validate_min=True,
    max_num=MAX_JOURNAL_LINES, absolute_max=MAX_JOURNAL_LINES, validate_max=True,
)


def line_inputs(formset) -> list[JournalLineInput]:
    return [f.to_input() for f in formset.forms if f.has_changed() and not f.cleaned_data.get("DELETE")]


class ReverseForm(BaseForm):
    date = BusinessDateField(label=_("Reversal date"))
