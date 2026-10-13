from django import forms
from django.utils.translation import gettext_lazy as _

from apps.contacts.selectors import customers_for, suppliers_for
from apps.core.forms import BaseForm, BaseModelForm, BusinessDateField, OrganizationFormMixin
from apps.inventory.selectors import cost_of_sales_accounts, inventory_accounts
from apps.ledger.domain import SystemRole
from apps.ledger.models import Account
from apps.ledger.selectors import accounts_for, cash_accounts, prepaid_tax_accounts, purchase_accounts, revenue_accounts
from apps.taxes.models import TaxRate
from apps.taxes.domain import PAYER_WITHHOLDING_RATES, PayerWithholding
from apps.taxes.selectors import tax_rates_for
from apps.tenants.models import BusinessPlace

from .facilities import ADDITIONAL_INFO, STAMPS, VatFacility
from .models import PAYER_WITHHOLDING, RECEIPT_WITHHOLDING, IncomeTaxWithheld, Invoice, InvoiceKind, Item, Payment
from .selectors import items_for
from .services import Facility, IncomeTaxInput, InvoiceLineInput, ReturnLineInput, returnable_quantity

FACILITY_SEPARATOR = "|"


def _facility_choices(table: dict) -> list:
    """Grouped per transaction code; values carry the code, since DJP reuses the same
    codes with another meaning under 07 and 08."""
    return [("", VatFacility.NONE.label)] + [
        (facility.label, [(f"{facility.value}{FACILITY_SEPARATOR}{code}", f"{facility.value} · {text}") for code, text in table[facility]])
        for facility in (VatFacility.NOT_COLLECTED, VatFacility.EXEMPT)
    ]


def line_accounts(organization, kind):
    return revenue_accounts(organization) if kind == InvoiceKind.SALES else purchase_accounts(organization)


def default_line_account(organization, kind):
    role = SystemRole.SALES_REVENUE if kind == InvoiceKind.SALES else SystemRole.PURCHASE_EXPENSE
    return accounts_for(organization).filter(system_role=role).first()


class InvoiceForm(OrganizationFormMixin, BaseModelForm):
    due_date = BusinessDateField(
        label=_("Due date"), required=False, help_text=_("Leave empty to use the contact's payment terms."),
    )
    # A PPN facility (code 07/08), for a PKP's sales only; see clean() for how it reaches the services.
    facility_info_choice = forms.ChoiceField(
        label=_("PPN facility"), required=False,
        help_text=_("Only for deliveries whose PPN isn't collected (code 07) or is exempt (code 08). Keep the PPN rate on the lines: the faktur shows the PPN, the buyer doesn't pay it."),
    )
    facility_stamp_choice = forms.ChoiceField(label=_("Facility stamp (cap fasilitas)"), required=False)
    supporting_document = forms.CharField(
        label=_("Supporting document"), max_length=100, required=False,
        help_text=Invoice._meta.get_field("supporting_document").help_text,
    )
    supporting_document_date = BusinessDateField(label=_("Supporting document date"), required=False)
    FACILITY_FIELDS = ("delivery_kind", "facility_info_choice", "facility_stamp_choice", "supporting_document", "supporting_document_date")

    class Meta:
        model = Invoice
        fields = ["contact", "date", "due_date", "business_place", "reference", "tax_invoice_number", "notes", "delivery_kind"]
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
        places = BusinessPlace.objects.for_organization(self.organization).filter(is_active=True)
        if kind == InvoiceKind.SALES and places.exists():
            self.fields["business_place"].queryset = places
            self.fields["business_place"].empty_label = _("Head office")
            self.fields["business_place"].help_text = _("Where the sale is made from: its NITKU and address go on the invoice and the faktur.")
        else:
            del self.fields["business_place"]
        if kind == InvoiceKind.PURCHASE or not self.organization.is_pkp:
            del self.fields["tax_invoice_number"]
            for name in self.FACILITY_FIELDS:
                del self.fields[name]
            return
        self.fields["delivery_kind"].help_text = _("A tourist's VAT refund (the customer identified by a foreign passport) or the sale of an asset not originally for sale.")
        self.fields["facility_info_choice"].choices = _facility_choices(ADDITIONAL_INFO)
        self.fields["facility_stamp_choice"].choices = [("", "—")] + _facility_choices(STAMPS)[1:]
        invoice = self.instance
        if invoice.pk and invoice.vat_facility:
            self.initial.setdefault("facility_info_choice", f"{invoice.vat_facility}{FACILITY_SEPARATOR}{invoice.facility_info}")
            self.initial.setdefault("facility_stamp_choice", f"{invoice.vat_facility}{FACILITY_SEPARATOR}{invoice.facility_stamp}")
        if invoice.pk:
            self.initial.setdefault("supporting_document", invoice.supporting_document)
            self.initial.setdefault("supporting_document_date", invoice.supporting_document_date)

    @property
    def main_fields(self):
        return [field for field in self.visible_fields() if field.name not in self.FACILITY_FIELDS]

    @property
    def facility_fields(self):
        return [field for field in self.visible_fields() if field.name in self.FACILITY_FIELDS]

    @property
    def shows_facility(self) -> bool:
        """Open the facility section: one is set, or it has errors."""
        return bool(self.instance.vat_facility or self.instance.delivery_kind or self.initial.get("supporting_document")) or any(
            name in self.errors for name in self.FACILITY_FIELDS
        ) or bool(self.data.get(self.add_prefix("facility_info_choice")) or self.data.get(self.add_prefix("delivery_kind")))

    def clean(self):
        """The services take the facility as one ``facility`` value (services.Facility)."""
        cleaned = super().clean()
        if "facility_info_choice" not in self.fields:
            return cleaned
        info = cleaned.pop("facility_info_choice", "") or ""
        stamp = cleaned.pop("facility_stamp_choice", "") or ""
        facility, _sep, info_code = info.partition(FACILITY_SEPARATOR)
        stamp_facility, _sep, stamp_code = stamp.partition(FACILITY_SEPARATOR)
        if stamp and stamp_facility != facility:
            self.add_error("facility_stamp_choice", _("Choose a stamp listed under the same transaction code as the facility."))
        elif facility and not stamp:
            self.add_error("facility_stamp_choice", _("A delivery with a PPN facility needs its stamp (cap fasilitas)."))
        cleaned["facility"] = Facility(
            vat_facility=facility, facility_info=info_code, facility_stamp=stamp_code if facility else "",
            supporting_document=cleaned.pop("supporting_document", "") or "",
            supporting_document_date=cleaned.pop("supporting_document_date", None),
        )
        return cleaned


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
    """A receipt may record income tax the customer withheld; a payment to a supplier the
    income tax we withhold (PPh 23 or 4(2), as base x rate)."""

    RECEIPT_FIELDS = ("income_tax_kind", "income_tax_withheld", "income_tax_account", "withholding_slip")
    PAYER_FIELDS = ("income_tax_kind", "income_tax_base", "income_tax_rate", "withholding_slip")
    INCOME_TAX_FIELDS = tuple(dict.fromkeys(RECEIPT_FIELDS + PAYER_FIELDS))

    class Meta:
        model = Payment
        fields = [
            "date", "amount", "vat_withheld", "cash_account", "method", "reference",
            "income_tax_kind", "income_tax_withheld", "income_tax_account", "income_tax_base", "income_tax_rate", "withholding_slip",
        ]
        field_classes = {"date": BusinessDateField}

    def __init__(self, *args, invoice=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["cash_account"].queryset = cash_accounts(self.organization)
        if invoice is not None and invoice.buyer_withholds_vat:
            self.fields["amount"].help_text = _("What this settles, including what the buyer withheld.")
            self.fields["vat_withheld"].help_text = _("The buyer is a VAT collector: it pays without the PPN and deposits the PPN itself.")
            self.fields["vat_withheld"].required = False
        else:
            del self.fields["vat_withheld"]
        if invoice is None:
            for name in self.INCOME_TAX_FIELDS:
                del self.fields[name]
            return
        keep = self.RECEIPT_FIELDS if invoice.is_sales else self.PAYER_FIELDS
        for name in self.INCOME_TAX_FIELDS:
            if name not in keep:
                del self.fields[name]
        kinds = self.fields["income_tax_kind"]
        if not invoice.is_sales:
            kinds.choices = [("", IncomeTaxWithheld.NONE.label)] + [(k.value, k.label) for k in PAYER_WITHHOLDING]
            kinds.help_text = _("If you withhold income tax from this supplier: you deposit it and give them a bukti potong.")
            self.fields["income_tax_base"].required = False
            self.fields["income_tax_base"].help_text = _("The gross amount the tax is on (the services or rent, without PPN).")
            self.fields["income_tax_rate"].help_text = _("Usually 2% for PPh 23 on services, 10% for PPh 4(2) on rent of land or buildings.")
            self.initial.setdefault("income_tax_base", invoice.subtotal)
            self.initial.setdefault("income_tax_rate", PAYER_WITHHOLDING_RATES[PayerWithholding.PPH_23])
            self.fields["withholding_slip"].help_text = _("The bukti potong number, if Coretax has issued it already; it can be added later.")
            return
        kinds.choices = [
            (value, label) for value, label in IncomeTaxWithheld.choices
            if value in ("", *RECEIPT_WITHHOLDING) and (value != IncomeTaxWithheld.PPH_FINAL or self.organization.uses_pph_final)
        ]
        kinds.help_text = _("If the buyer withheld income tax and gave you a bukti potong.")
        self.fields["income_tax_withheld"].required = False
        accounts = prepaid_tax_accounts(self.organization)
        self.fields["income_tax_account"].queryset = accounts
        self.fields["income_tax_account"].initial = accounts.first()
        self.fields["income_tax_account"].help_text = _("For PPh 22 or 23: the tax prepaid, credited in your annual income tax return.")

    @property
    def main_fields(self):
        return [field for field in self.visible_fields() if field.name not in self.INCOME_TAX_FIELDS]

    @property
    def income_tax_fields(self):
        return [field for field in self.visible_fields() if field.name in self.INCOME_TAX_FIELDS]

    def clean(self):
        cleaned = super().clean()
        if "income_tax_kind" not in self.fields:
            return cleaned
        kind = cleaned.get("income_tax_kind")
        if "income_tax_base" in self.fields:  # we withhold from a supplier
            if kind and not cleaned.get("income_tax_base"):
                self.add_error("income_tax_base", _("Enter the amount the tax is withheld on."))
            if kind and not cleaned.get("income_tax_rate"):
                self.add_error("income_tax_rate", _("Enter the withholding rate (%)."))
            return cleaned
        amount = cleaned.get("income_tax_withheld")
        if kind and not amount:
            self.add_error("income_tax_withheld", _("Enter the income tax withheld."))
        elif amount and not kind:
            self.add_error("income_tax_kind", _("Choose which income tax the buyer withheld."))
        elif kind in (IncomeTaxWithheld.PPH_22, IncomeTaxWithheld.PPH_23) and not cleaned.get("income_tax_account"):
            self.add_error("income_tax_account", _("Choose the prepaid tax account."))
        return cleaned

    def service_kwargs(self) -> dict:
        """What ``services.record_payment`` takes."""
        data = dict(self.cleaned_data)
        if "income_tax_kind" not in data:
            return data
        kind = data.pop("income_tax_kind")
        slip = data.pop("withholding_slip")
        if "income_tax_base" in data:
            base, rate = data.pop("income_tax_base"), data.pop("income_tax_rate")
            data["income_tax"] = IncomeTaxInput(kind=kind, base=base or 0, rate=rate, slip=slip) if kind else IncomeTaxInput()
            return data
        account = data.pop("income_tax_account")
        data["income_tax"] = IncomeTaxInput(
            kind=kind, amount=data.pop("income_tax_withheld") or 0,
            account=account if kind != IncomeTaxWithheld.PPH_FINAL else None, slip=slip,
        )
        return data


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
        for line in invoice.lines.prefetch_related("returns__return_note").select_related("item"):
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


class TaxInvoiceNumberForm(BaseForm):
    number = forms.CharField(label=_("Faktur Pajak number"), max_length=30, help_text=_("As issued by Coretax, e.g. 04002600000012345."))
