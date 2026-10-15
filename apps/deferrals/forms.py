from django import forms
from django.utils.translation import gettext_lazy as _

from apps.core.forms import BaseModelForm, BusinessDateField, OrganizationFormMixin

from .domain import DeferralKind
from .models import Deferral
from .selectors import balance_accounts, recognition_accounts

RECLASSIFY_LABEL = {
    DeferralKind.PREPAID_EXPENSE: _("The bill or payment was recorded as an expense: move the amount to the prepaid account"),
    DeferralKind.DEFERRED_REVENUE: _("The invoice or receipt was recorded as revenue: move the amount to the received in advance account"),
}


class DeferralForm(OrganizationFormMixin, BaseModelForm):
    reclassify = forms.BooleanField(required=False, initial=True)

    class Meta:
        model = Deferral
        fields = ["description", "amount", "recorded_on", "starts_on", "months", "balance_account", "recognition_account"]
        field_classes = {"recorded_on": BusinessDateField, "starts_on": BusinessDateField}

    def __init__(self, *args, kind: str, **kwargs):
        super().__init__(*args, **kwargs)
        self.kind = kind
        self.fields["balance_account"].queryset = balance = balance_accounts(self.organization, kind)
        self.fields["recognition_account"].queryset = recognition = recognition_accounts(self.organization, kind)
        self.fields["reclassify"].label = RECLASSIFY_LABEL[kind]
        self.fields["reclassify"].help_text = _("Leave it off when the bill or invoice line already used that account.")
        self.fields["starts_on"].help_text = _("Any day of the first month the amount covers.")
        name = "biaya dibayar" if kind == DeferralKind.PREPAID_EXPENSE else "diterima di muka"
        self.initial.setdefault("balance_account", balance.filter(name__icontains=name).first() or balance.first())
        self.initial.setdefault("recognition_account", recognition.filter(name__icontains="sewa").first())

    def service_kwargs(self) -> dict:
        return {"kind": self.kind, **self.cleaned_data}
