from datetime import date

from django import forms
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.forms import BaseForm, BusinessDateField, OrganizationFormMixin
from apps.ledger.models import Account
from apps.ledger.selectors import accounts_for


def start_of_year(on: date) -> date:
    return on.replace(month=1, day=1)


class PeriodForm(BaseForm):
    date_from = BusinessDateField(label=_("From"))
    date_to = BusinessDateField(label=_("To"))

    def __init__(self, data=None, **kwargs):
        today = timezone.localdate()
        defaults = {"date_from": start_of_year(today).isoformat(), "date_to": today.isoformat()}
        merged = {**defaults, **{k: v for k, v in (data or {}).items() if v}}
        super().__init__(merged, **kwargs)

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("date_from") and cleaned.get("date_to") and cleaned["date_from"] > cleaned["date_to"]:
            raise forms.ValidationError(_("The start date must be before the end date."))
        return cleaned


class AsOfForm(BaseForm):
    as_of = BusinessDateField(label=_("As of"))

    def __init__(self, data=None, **kwargs):
        merged = {"as_of": timezone.localdate().isoformat(), **{k: v for k, v in (data or {}).items() if v}}
        super().__init__(merged, **kwargs)


class LedgerForm(OrganizationFormMixin, PeriodForm):
    account = forms.ModelChoiceField(label=_("Account"), queryset=Account.objects.none(), required=False)

    def __init__(self, data=None, organization=None, **kwargs):
        super().__init__(data, organization=organization, **kwargs)
        self.fields["account"].queryset = accounts_for(organization)
