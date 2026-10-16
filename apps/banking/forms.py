from django import forms
from django.utils.translation import gettext_lazy as _

from apps.core.forms import BaseForm, BusinessDateField, OrganizationFormMixin
from apps.ledger.selectors import accounts_for


class PostLineForm(OrganizationFormMixin, BaseForm):
    counter_account = forms.ModelChoiceField(label=_("Other account"), queryset=None,
                                             help_text=_("E.g. Beban Administrasi Bank for bank charges, Pendapatan Bunga Bank for interest."))
    description = forms.CharField(label=_("Description"), max_length=255, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["counter_account"].queryset = accounts_for(self.organization, active_only=True).filter(system_role__isnull=True)


class AsOfForm(BaseForm):
    as_of = BusinessDateField(label=_("As at"), required=False)
