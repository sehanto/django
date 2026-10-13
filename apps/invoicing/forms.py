from django import forms
from django.utils.translation import gettext_lazy as _

from apps.contacts.selectors import customers_for, suppliers_for
from apps.core.forms import BaseForm, BaseModelForm, BusinessDateField, OrganizationFormMixin
from apps.inventory.selectors import cost_of_sales_accounts, inventory_accounts
from apps.ledger.domain import SystemRole
from apps.ledger.models import Account
from apps.ledger.selectors import accounts_for, cash_accounts, purchase_accounts, revenue_accounts
from apps.taxes.models import TaxRate
from apps.taxes.selectors import tax_rates_for

from .models import Invoice, InvoiceKind, Item, Payment
from .selectors import items_for
from .services import InvoiceLineInput, ReturnLineInput, returnable_quantity


def line_accounts(organization, kind):
    return revenue_accounts(organization) if kind == InvoiceKind.SALES else purchase_accounts(organization)


def default_line_account(organization, kind):
    role = SystemRole.SALES_REVENUE if kind == InvoiceKind.SALES else SystemRole.PURCHASE_EXPENSE
    return accounts_for(organization).filter(system_role=role).first()


class InvoiceForm(OrganizationFormMixin, BaseModelForm):
    due_date = BusinessDateField(
        label=_("Due date"), required=False, help_text=_("Leave empty to use the contact's payment terms."),
    )

    class Meta:
        model = Invoice
        fields = ["contact", "date", "due_date", "reference", "tax_invoice_number", "notes"]
        field_classes = {"date": BusinessDateField}
        widgets = {"notes": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *args, kind, **kwargs):
        super().__init__(*args, **kwargs)
        self.kind = kind
        if kind == InvoiceKind.SALES:
            self.fields["contact"].queryset = customers_for(self.organization)
            self.fields["contact"].label = _("Customer")
        else:
            self.fields["contact"].queryset = suppliers_for(self.organization)
            self.fields["contact"].label = _("Supplier")
        if kind == InvoiceKind.PURCHASE or not self.organization.is_pkp:
            del self.fields["tax_invoice_number"]


class InvoiceLineForm(OrganizationFormMixin, BaseForm):
    item = forms.ModelChoiceField(label=_("Item"), queryset=Item.objects.none(), required=False)
    description = forms.CharField(label=_("Item description"), max_length=255)
    account = forms.ModelChoiceField(label=_("Account"), queryset=Account.objects.none())
    quantity = forms.DecimalField(label=_("Qty"), max_digits=14, decimal_places=3, min_value=0.001, initial=1)
    unit_price = forms.DecimalField(label=_("Price"), max_digits=18, decimal_places=2, min_value=0)
    discount_percent = forms.DecimalField(
        label=_("Disc. %"), max_digits=5, decimal_places=2, min_value=0, max_value=100, required=False
    )
    tax_rate = forms.ModelChoiceField(label=_("Tax"), queryset=TaxRate.objects.none(), required=False)

    def __init__(self, *args, kind, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["item"].queryset = items_for(self.organization, active_only=True)
        self.fields["account"].queryset = line_accounts(self.organization, kind)
        self.fields["account"].initial = default_line_account(self.organization, kind)
        self.fields["tax_rate"].queryset = tax_rates_for(self.organization, active_only=True)
        if kind == InvoiceKind.SALES and not self.organization.is_pkp:
            self.fields["tax_rate"].disabled = True

    def to_input(self) -> InvoiceLineInput:
        data = self.cleaned_data
        return InvoiceLineInput(
            description=data["description"],
            account=data["account"],
            quantity=data["quantity"],
            unit_price=data["unit_price"],
            discount_percent=data.get("discount_percent") or 0,
            tax_rate=data.get("tax_rate"),
            item=data.get("item"),
        )


MAX_INVOICE_LINES = 100
InvoiceLineFormSet = forms.formset_factory(
    InvoiceLineForm, extra=0, min_num=1, validate_min=True, can_delete=True,
    max_num=MAX_INVOICE_LINES, absolute_max=MAX_INVOICE_LINES, validate_max=True,
)


def line_inputs(formset) -> list[InvoiceLineInput]:
    return [f.to_input() for f in formset.forms if f.cleaned_data and not f.cleaned_data.get("DELETE")]


class PaymentForm(OrganizationFormMixin, BaseModelForm):
    class Meta:
        model = Payment
        fields = ["date", "amount", "cash_account", "method", "reference"]
        field_classes = {"date": BusinessDateField}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["cash_account"].queryset = cash_accounts(self.organization)


class VoidForm(BaseForm):
    date = BusinessDateField(label=_("Void date"))


class ItemForm(OrganizationFormMixin, BaseModelForm):
    class Meta:
        model = Item
        fields = [
            "code", "name", "type", "unit", "sale_price", "purchase_price",
            "income_account", "expense_account", "tax_rate",
            "track_inventory", "inventory_account", "cogs_account", "is_active",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        organization = self.organization
        self.fields["income_account"].queryset = revenue_accounts(organization)
        self.fields["expense_account"].queryset = purchase_accounts(organization)
        self.fields["expense_account"].help_text = _("For goods whose stock isn't tracked, and for services.")
        self.fields["tax_rate"].queryset = tax_rates_for(organization, active_only=True)
        self.fields["inventory_account"].queryset = inventory_accounts(organization)
        self.fields["cogs_account"].queryset = cost_of_sales_accounts(organization)
        # Suggested accounts for turning tracking on, for new and existing items alike.
        if not self.initial.get("inventory_account"):
            self.initial["inventory_account"] = inventory_accounts(organization).first()
        if not self.initial.get("cogs_account"):
            self.initial["cogs_account"] = cost_of_sales_accounts(organization).filter(system_role__isnull=False).first()
        if self.instance.pk and self.instance.stock_movements.exists():  # the database refuses these changes
            for name in Item.FIXED_ONCE_STOCKED:
                self.fields[name].disabled = True
            self.fields["track_inventory"].help_text = _("This item has stock movements, so its type, stock tracking and accounts are fixed.")

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("track_inventory"):
            if cleaned.get("type") != Item.Type.GOODS:
                self.add_error("track_inventory", _("Only goods have stock."))
            for name in ("inventory_account", "cogs_account"):
                if not cleaned.get(name) and name not in self.errors:
                    self.add_error(name, _("Required for an item whose stock is tracked."))
        return cleaned


class ReturnForm(OrganizationFormMixin, BaseForm):
    """One quantity field per line of the invoice that can still be returned."""

    date = BusinessDateField(label=_("Return date"))
    reason = forms.CharField(label=_("Reason"), max_length=255, help_text=_("E.g. damaged goods, wrong item delivered."))
    cash_account = forms.ModelChoiceField(
        label=_("Refund through"), queryset=Account.objects.none(), required=False,
        help_text=_("Only needed when the return is more than what is still owed on the invoice."),
    )

    def __init__(self, *args, invoice, **kwargs):
        super().__init__(*args, **kwargs)
        self.invoice = invoice
        self.fields["cash_account"].queryset = cash_accounts(self.organization)
        self.returnable = []
        for line in invoice.lines.prefetch_related("returns").select_related("item"):
            available = returnable_quantity(line)
            if available <= 0:
                continue
            name = f"line_{line.pk}"
            self.fields[name] = forms.DecimalField(
                label=line.description, required=False, min_value=0, max_value=available, max_digits=14, decimal_places=3,
                help_text=_("Up to %(quantity)s") % {"quantity": f"{available.normalize():f}"},
            )
            self.returnable.append((line, name, available))

    @property
    def line_fields(self):
        return [(line, self[name], available) for line, name, available in self.returnable]

    def clean(self):
        cleaned = super().clean()
        if not any(cleaned.get(name) for _line, name, _a in self.returnable):
            raise forms.ValidationError(_("Enter the quantity returned for at least one line."))
        return cleaned

    def to_input(self) -> dict:
        data = self.cleaned_data
        return {
            "date": data["date"], "reason": data["reason"], "cash_account": data.get("cash_account"),
            "lines": [ReturnLineInput(line, data[name]) for line, name, _a in self.returnable if data.get(name)],
        }
