from django import forms
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from apps.core.forms import BaseForm, BaseModelForm, BusinessDateField, OrganizationFormMixin
from apps.core.money import MONEY_DECIMAL_PLACES, MONEY_MAX_DIGITS
from apps.core.validators import ntpn_validator
from apps.ledger.domain import SystemRole
from apps.ledger.models import Account
from apps.ledger.selectors import accounts_for, cash_accounts, prepaid_tax_accounts

from . import services, tax_objects
from .domain import Month, PayerWithholding
from .models import TaxRate
from .selectors import tax_rate_is_used


class TaxRateForm(OrganizationFormMixin, BaseModelForm):
    LOCKED_WHEN_USED = ("name", "rate", "dpp_numerator", "dpp_denominator", "is_specific_amount", "luxury_rate")

    class Meta:
        model = TaxRate
        fields = ["name", "rate", "dpp_numerator", "dpp_denominator", "is_specific_amount", "luxury_rate", "is_active"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.locked = bool(self.instance.pk) and tax_rate_is_used(self.instance)
        if self.locked:  # the database refuses these changes anyway; say so up front
            for name in self.LOCKED_WHEN_USED:
                self.fields[name].disabled = True
            self.fields["is_active"].help_text = _(
                "This rate is used on issued invoices, so only its active status can change. "
                "To charge a different rate, deactivate it and add a new one."
            )


class PphFinalPaymentForm(OrganizationFormMixin, BaseForm):
    month = forms.TypedChoiceField(widget=forms.HiddenInput, choices=Month.choices, coerce=lambda value: Month(int(value)))
    paid_on = BusinessDateField(label=_("Payment date"))
    cash_account = forms.ModelChoiceField(label=_("Paid from"), queryset=Account.objects.none())
    ntpn = forms.CharField(label=_("NTPN"), max_length=32, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["cash_account"].queryset = cash_accounts(self.organization)

    def clean_ntpn(self):
        ntpn = "".join(ch for ch in self.cleaned_data["ntpn"] if ch.isalnum()).upper()  # drop spaces/dashes
        if ntpn:
            ntpn_validator(ntpn)
        return ntpn


class WithholdingDepositForm(OrganizationFormMixin, BaseForm):
    kind = forms.ChoiceField(widget=forms.HiddenInput, choices=PayerWithholding.choices)
    month = forms.TypedChoiceField(widget=forms.HiddenInput, choices=Month.choices, coerce=lambda value: Month(int(value)))
    paid_on = BusinessDateField(label=_("Payment date"))
    cash_account = forms.ModelChoiceField(label=_("Paid from"), queryset=Account.objects.none())
    ntpn = forms.CharField(label=_("NTPN"), max_length=32, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["cash_account"].queryset = cash_accounts(self.organization)

    def clean_ntpn(self):
        ntpn = "".join(ch for ch in self.cleaned_data["ntpn"] if ch.isalnum()).upper()
        if ntpn:
            ntpn_validator(ntpn)
        return ntpn


class TaxObjectForm(BaseForm):
    """The tax object code of a supplier payment recorded without one."""

    def __init__(self, *args, **kwargs):
        from apps.invoicing.forms import tax_object_field  # invoicing's payment form uses the same list

        super().__init__(*args, auto_id=False, **kwargs)
        self.fields["code"] = tax_object_field(required=True)
        self.fields["code"].widget.attrs.update({"class": "form-select form-select-sm", "aria-label": self.fields["code"].label})


def _clean_ntpn(value: str) -> str:
    ntpn = "".join(ch for ch in value if ch.isalnum()).upper()
    if ntpn:
        ntpn_validator(ntpn)
    return ntpn


def _money(label, required=True, help_text=""):
    return forms.DecimalField(label=label, max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES, min_value=0,
                              required=required, initial=0, help_text=help_text)


class ImportDeclarationForm(OrganizationFormMixin, BaseForm):
    number = forms.CharField(label=_("PIB no."), max_length=50)
    on = BusinessDateField(label=_("PIB date"))
    supplier_name = forms.CharField(label=_("Foreign supplier"), max_length=200)
    bill = forms.ModelChoiceField(label=_("Supplier's bill"), queryset=Account.objects.none(), required=False,
                                  help_text=_("The bill for the goods themselves (without PPN), if recorded."))
    customs_value = _money(_("Import value (DPP)"), help_text=_("CIF value plus import duty: the base of PPN impor."))
    import_duty = _money(_("Import duty (bea masuk)"))
    vat = _money(_("PPN impor"))
    luxury_tax = _money(_("PPnBM impor"))
    pph22 = _money(_("PPh 22 impor"))
    cost_account = forms.ModelChoiceField(label=_("Cost account"), queryset=Account.objects.none(),
                                          help_text=_("Where the import duty and PPnBM go (and the PPN impor, for a business that isn't a PKP)."))
    prepaid_tax_account = forms.ModelChoiceField(label=_("Prepaid tax account"), queryset=Account.objects.none(), required=False,
                                                 help_text=_("For the PPh 22 impor, credited in the annual income tax return."))
    cash_account = forms.ModelChoiceField(label=_("Paid from"), queryset=Account.objects.none())
    ntpn = forms.CharField(label=_("NTPN"), max_length=32, required=False)

    def __init__(self, *args, **kwargs):
        from apps.invoicing.models import Invoice, InvoiceKind  # invoicing depends on taxes

        super().__init__(*args, **kwargs)
        accounts = accounts_for(self.organization, active_only=True).filter(
            Q(system_role__isnull=True) | Q(system_role=SystemRole.PURCHASE_EXPENSE),
        )
        self.fields["bill"].queryset = Invoice.objects.for_organization(self.organization).filter(
            kind=InvoiceKind.PURCHASE, status__in=Invoice.ISSUED_STATUSES,
        ).order_by("-date", "-id")
        self.fields["cost_account"].queryset = accounts.filter(category__in=services.IMPORT_COST_CATEGORIES)
        prepaid = prepaid_tax_accounts(self.organization)
        self.fields["prepaid_tax_account"].queryset = prepaid
        self.fields["prepaid_tax_account"].initial = prepaid.first()
        self.fields["cash_account"].queryset = cash_accounts(self.organization)

    def clean_ntpn(self):
        return _clean_ntpn(self.cleaned_data["ntpn"])


class SelfDepositForm(OrganizationFormMixin, BaseForm):
    tax_object = forms.ChoiceField(label=_("Tax object code"))
    tax_base = _money(_("Income (tax base)"))
    document_number = forms.CharField(label=_("Document no."), max_length=100, help_text=_("e.g. the invoice for the rent."))
    document_date = BusinessDateField(label=_("Document date"), help_text=_("Its month is the tax period."))
    invoice = forms.ModelChoiceField(label=_("Sales invoice"), queryset=Account.objects.none(), required=False)
    paid_on = BusinessDateField(label=_("Payment date"))
    cash_account = forms.ModelChoiceField(label=_("Paid from"), queryset=Account.objects.none())
    ntpn = forms.CharField(label=_("NTPN"), max_length=32, required=False)

    def __init__(self, *args, **kwargs):
        from apps.invoicing.models import Invoice, InvoiceKind  # invoicing depends on taxes

        super().__init__(*args, **kwargs)
        self.fields["tax_object"].choices = [("", "---------")] + [
            (code, f"{code} {name} ({rate.normalize():f}%)"[:160]) for code, (name, rate) in sorted(tax_objects.SELF_DEPOSIT.items())
        ]
        self.fields["invoice"].queryset = Invoice.objects.for_organization(self.organization).filter(
            kind=InvoiceKind.SALES, status__in=Invoice.ISSUED_STATUSES,
        ).order_by("-date", "-id")
        self.fields["cash_account"].queryset = cash_accounts(self.organization)

    def clean_ntpn(self):
        return _clean_ntpn(self.cleaned_data["ntpn"])
