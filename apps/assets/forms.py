from django import forms
from django.utils.translation import gettext_lazy as _

from apps.core.forms import BaseForm, BaseModelForm, BusinessDateField, DateInput, OrganizationFormMixin
from apps.core.money import MONEY_DECIMAL_PLACES, MONEY_MAX_DIGITS
from apps.ledger.models import Account
from apps.ledger.selectors import cash_accounts

from .models import FixedAsset
from .selectors import accumulated_depreciation_accounts, depreciation_expense_accounts, fixed_asset_accounts, gain_loss_accounts

DEPRECIATION_FIELDS = ("useful_life_months", "residual_value", "accumulated_depreciation_account", "depreciation_expense_account")


class FixedAssetForm(OrganizationFormMixin, BaseModelForm):
    class Meta:
        model = FixedAsset
        fields = [
            "name", "description", "asset_account", "acquisition_date", "cost", "is_depreciable", "useful_life_months",
            "residual_value", "accumulated_depreciation_account", "depreciation_expense_account", "depreciated_before_until",
        ]
        field_classes = {"acquisition_date": BusinessDateField}
        widgets = {"depreciated_before_until": DateInput()}

    def __init__(self, *args, locked=False, **kwargs):
        super().__init__(*args, **kwargs)
        organization = self.organization
        self.fields["asset_account"].queryset = fixed_asset_accounts(organization)
        self.fields["accumulated_depreciation_account"].queryset = accumulated_depreciation_accounts(organization)
        self.fields["depreciation_expense_account"].queryset = expense = depreciation_expense_accounts(organization)
        self.fields["residual_value"].required = False
        if not self.instance.pk:
            self.initial.setdefault("accumulated_depreciation_account", accumulated_depreciation_accounts(organization).first())
            self.initial.setdefault("depreciation_expense_account", expense.filter(name__icontains="penyusutan").first())
        if locked:  # the database refuses these changes; say so up front
            for name in FixedAsset.FIXED_ONCE_DEPRECIATED:
                if name in self.fields:
                    self.fields[name].disabled = True
            self.fields["cost"].help_text = _("Depreciation has been posted for this asset, so its figures are fixed. The name and description can still change.")

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("is_depreciable"):
            for name in DEPRECIATION_FIELDS:
                if name != "residual_value" and not cleaned.get(name) and name not in self.errors:
                    self.add_error(name, _("Required for a depreciable asset."))
        return cleaned


class DisposalForm(OrganizationFormMixin, BaseForm):
    on = BusinessDateField(label=_("Disposal date"))
    proceeds = forms.DecimalField(
        label=_("Sale proceeds"), max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES, min_value=0, required=False,
        help_text=_("0 for a write-off."),
    )
    cash_account = forms.ModelChoiceField(label=_("Received in"), queryset=Account.objects.none(), required=False)
    gain_loss_account = forms.ModelChoiceField(
        label=_("Gain or loss account"), queryset=Account.objects.none(), required=False,
        help_text=_("Other income for a gain, other expense for a loss."),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["cash_account"].queryset = cash_accounts(self.organization)
        self.fields["gain_loss_account"].queryset = gain_loss_accounts(self.organization)

