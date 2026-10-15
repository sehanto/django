from django import forms
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.forms import BaseForm, BaseModelForm, BusinessDateField, GridFormMixin, OrganizationFormMixin
from apps.core.money import ZERO
from apps.core.validators import EARLIEST_BUSINESS_DATE

from .models import Account, JournalEntry
from .selectors import accounts_for
from .services import JournalLineInput


class AccountForm(OrganizationFormMixin, BaseModelForm):
    class Meta:
        model = Account
        fields = ["code", "name", "category", "is_contra", "description", "is_active"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        account = self.instance
        if account.pk and account.lines.exists():  # the database refuses these changes; say so up front
            for name in Account.FIXED_ONCE_USED:
                self.fields[name].disabled = True
            self.fields["category"].help_text = _(
                "This account has transactions, so its category and contra setting are fixed. "
                "Its code and name can still change."
            )
        if account.pk and account.system_role:
            self.fields["is_active"].disabled = True
            self.fields["is_active"].help_text = _("System accounts are always active.")


class JournalEntryForm(OrganizationFormMixin, BaseModelForm):
    class Meta:
        model = JournalEntry
        fields = ["date", "description", "reference", "restates_year"]
        field_classes = {"date": BusinessDateField}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        this_year = timezone.localdate().year
        years = range(this_year - 1, EARLIEST_BUSINESS_DATE.year - 1, -1)
        self.fields["restates_year"] = forms.TypedChoiceField(
            label=_("Correction of an earlier year's error"), required=False, coerce=int, empty_value=None,
            choices=[("", _("No: an entry of its own date"))] + [(year, str(year)) for year in years],
            initial=self.instance.restates_year,
            help_text=JournalEntry._meta.get_field("restates_year").help_text,
        )


class JournalLineForm(GridFormMixin, OrganizationFormMixin, BaseForm):
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
