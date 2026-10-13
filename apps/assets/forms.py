from django import forms
from django.utils.translation import gettext_lazy as _

from apps.contacts.models import Contact
from apps.contacts.selectors import customers_for
from apps.core.forms import BaseForm, BaseModelForm, BusinessDateField, DateInput, OrganizationFormMixin
from apps.core.money import MONEY_DECIMAL_PLACES, MONEY_MAX_DIGITS
from apps.ledger.models import Account
from apps.ledger.selectors import cash_accounts
from apps.taxes.models import TaxRate
from apps.taxes.selectors import tax_rates_for

from .domain import MAX_USEFUL_LIFE_MONTHS, DepreciationMethod
from .models import FixedAsset
from .services import InvoicedSale
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
    customer = forms.ModelChoiceField(
        label=_("Invoice to"), queryset=Contact.objects.none(), required=False,
        help_text=_("To sell on credit: a sales invoice is issued for the proceeds instead of cash being received."),
    )
    tax_rate = forms.ModelChoiceField(
        label=_("PPN on the invoice"), queryset=TaxRate.objects.none(), required=False,
        help_text=_("A PKP charges PPN on selling business assets (Art. 16D UU PPN, Faktur Pajak code 09)."),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["cash_account"].queryset = cash_accounts(self.organization)
        self.fields["gain_loss_account"].queryset = gain_loss_accounts(self.organization)
        self.fields["customer"].queryset = customers_for(self.organization)
        if self.organization.is_pkp:
            self.fields["tax_rate"].queryset = tax_rates_for(self.organization, active_only=True)
        else:
            del self.fields["tax_rate"]

    def service_kwargs(self) -> dict:
        """What ``services.dispose_asset`` takes."""
        data = dict(self.cleaned_data)
        customer, tax_rate = data.pop("customer", None), data.pop("tax_rate", None)
        if customer is not None:
            data["sale"] = InvoicedSale(customer, tax_rate)
            data["cash_account"] = None
        return data



class RevisionForm(BaseForm):
    effective_from = BusinessDateField(label=_("Applies from"), help_text=_("The change applies from the start of this month."))
    useful_life_months = forms.IntegerField(
        label=_("Useful life (months)"), min_value=1, max_value=MAX_USEFUL_LIFE_MONTHS,
        help_text=_("The whole useful life, counted from the month of acquisition."),
    )
    residual_value = forms.DecimalField(
        label=_("Residual value"), max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES, min_value=0, required=False,
    )
    method = forms.ChoiceField(
        label=_("Depreciation method"), choices=DepreciationMethod.choices,
        help_text=_("Declining balance continues at twice the rate of the remaining life."),
    )
    reason = forms.CharField(label=_("Reason"), max_length=255, help_text=_("E.g. the machine will be used two years longer than expected."))
