from django import forms
from django.utils.translation import gettext_lazy as _

from apps.accounts import two_factor
from apps.contacts.validators import nitku_validator, npwp_format_validator
from django.utils import timezone

from apps.core.forms import BaseForm, BaseModelForm, DateInput, DigitsField, OrganizationFormMixin
from apps.core.validators import nib_validator
from apps.regions.forms import RegionFieldsMixin

from .models import BusinessPlace, Organization
from .roles import Role


class OrganizationSettingsForm(RegionFieldsMixin, OrganizationFormMixin, BaseModelForm):
    npwp = DigitsField(label=_("tax ID (NPWP)"), max_length=16, required=False, validators=[npwp_format_validator],
                       help_text=_("15 or 16 digits; dots and dashes are removed."))
    nib = DigitsField(label=_("business ID (NIB)"), max_length=13, required=False, validators=[nib_validator])

    class Meta:
        model = Organization
        fields = [
            "name", "legal_form", "npwp", "nib", "address", "village", "phone", "email",
            "is_pkp", "is_retailer", "uses_pph_final", "inventory_cost_formula", "depreciation_method", "additional_notes",
            "lock_date", "require_two_factor",
        ]
        widgets = {
            "address": forms.Textarea(attrs={"rows": 2}), "additional_notes": forms.Textarea(attrs={"rows": 4}),
            "lock_date": DateInput(),
        }

    def __init__(self, *args, acting_user, **kwargs):
        self.acting_user = acting_user
        super().__init__(*args, **kwargs)

    def clean_require_two_factor(self):
        required = self.cleaned_data["require_two_factor"]
        if required and not two_factor.is_enabled(self.acting_user):
            # Otherwise the person setting the rule would be the first one locked out.
            raise forms.ValidationError(_("Turn on two-factor authentication for your own account first (Profile → Security)."))
        return required

    def clean_lock_date(self):
        lock_date = self.cleaned_data["lock_date"]
        if lock_date and lock_date > timezone.localdate():
            raise forms.ValidationError(_("The lock date cannot be in the future."))
        return lock_date


class OrganizationCreateForm(BaseForm):
    name = forms.CharField(label=_("Business name"), max_length=200)
    legal_form = forms.ChoiceField(label=_("Legal form"), choices=Organization.LegalForm.choices)


class AddMemberForm(OrganizationFormMixin, BaseForm):
    email = forms.EmailField(label=_("User email"))
    role = forms.ChoiceField(
        label=_("Role"),
        choices=[(r.value, r.label) for r in Role if r != Role.OWNER],
        initial=Role.ACCOUNTANT,
    )


class BusinessPlaceForm(RegionFieldsMixin, OrganizationFormMixin, BaseModelForm):
    nitku = DigitsField(label=_("NITKU"), max_length=22, validators=[nitku_validator],
                        help_text=BusinessPlace._meta.get_field("nitku").help_text)

    class Meta:
        model = BusinessPlace
        fields = ["name", "nitku", "address", "village", "is_active"]
        widgets = {"address": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.organization = self.organization  # its NITKU is checked against the organization's NPWP

    def clean_nitku(self):
        nitku = self.cleaned_data["nitku"]
        others = BusinessPlace.objects.for_organization(self.organization).filter(nitku=nitku).exclude(pk=self.instance.pk)
        if others.exists():
            raise forms.ValidationError(_("This NITKU is already registered as another place."))
        return nitku
