from django import forms
from django.utils.translation import gettext_lazy as _

from apps.core.forms import BaseForm, BusinessDateField, OrganizationFormMixin
from apps.core.money import MONEY_DECIMAL_PLACES, MONEY_MAX_DIGITS
from apps.invoicing.models import Item
from apps.ledger.models import Account

from .selectors import adjustment_accounts, tracked_items


class AdjustmentForm(OrganizationFormMixin, BaseForm):
    item = forms.ModelChoiceField(label=_("Item"), queryset=Item.objects.none())
    on = BusinessDateField(label=_("Date"))
    quantity = forms.DecimalField(
        label=_("Quantity"), max_digits=14, decimal_places=3,
        help_text=_("Positive to add stock (e.g. opening stock, a count surplus), negative to remove it (shrinkage, damage)."),
    )
    unit_cost = forms.DecimalField(
        label=_("Unit cost"), max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES, min_value=0, required=False,
        help_text=_("For stock added; empty uses the current average cost. Stock removed always leaves at its cost under the cost formula."),
    )
    counter_account = forms.ModelChoiceField(
        label=_("Other side of the entry"), queryset=Account.objects.none(),
        help_text=_("E.g. Modal Pemilik for opening stock, Beban Pokok Penjualan or an other expense for shrinkage."),
    )
    reason = forms.CharField(label=_("Reason"), max_length=200)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["item"].queryset = tracked_items(self.organization).filter(is_active=True)
        self.fields["counter_account"].queryset = adjustment_accounts(self.organization)
