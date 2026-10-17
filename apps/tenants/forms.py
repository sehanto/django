from django import forms
from django.utils.translation import gettext_lazy as _

from apps.accounts import two_factor
from apps.contacts.validators import nitku_validator, npwp_format_validator
from django.utils import timezone

from apps.core.forms import BaseForm, BaseModelForm, DateInput, DigitsField, OrganizationFormMixin
from apps.core.validators import EMAIL_MAX_LENGTH, nib_validator
from apps.regions.forms import RegionFieldsMixin

from .models import BusinessPlace, Membership, Organization
from .roles import Role


class OrganizationSettingsForm(RegionFieldsMixin, OrganizationFormMixin, BaseModelForm):
    npwp = DigitsField(label=_("tax ID (NPWP)"), max_length=16, required=False, validators=[npwp_format_validator],
                       help_text=_("15 or 16 digits; dots and dashes are removed."))
    nib = DigitsField(label=_("business ID (NIB)"), max_length=13, required=False, validators=[nib_validator])

    class Meta:
        model = Organization
        fields = [
            "name", "legal_form", "npwp", "nib", "address", "village", "phone", "email",
            "is_pkp", "is_retailer", "uses_pph_final", "pph_final_since", "inventory_cost_formula", "depreciation_method", "additional_notes",
            "lock_date", "require_two_factor",
        ]
        widgets = {
            "additional_notes": forms.Textarea(attrs={"rows": 2}),
            "lock_date": DateInput(),
        }

    def __init__(self, *args, acting_user, **kwargs):
        self.acting_user = acting_user
        super().__init__(*args, **kwargs)
        # SAK EMKM 7.5: a policy, once applied, is applied consistently. Changing it means
        # restating the books (7.8-7.9), which is an accountant's adjusting entry, not a setting.
        for name, (in_use, help_text) in policies_in_use(self.instance).items():
            if in_use:
                self.fields[name].disabled = True
                self.fields[name].help_text = help_text

    def clean_require_two_factor(self):
        required = self.cleaned_data["require_two_factor"]
        if required and not two_factor.is_enabled(self.acting_user):
            # Otherwise the person setting the rule would be the first one locked out.
            raise forms.ValidationError(_("Turn on two-factor authentication for your own account first (Profile → Security)."))
        return required

    def clean_pph_final_since(self):
        year = self.cleaned_data["pph_final_since"]
        if year and year > timezone.localdate().year:
            raise forms.ValidationError(_("The year cannot be in the future."))
        return year

    def clean_lock_date(self):
        lock_date = self.cleaned_data["lock_date"]
        if lock_date and lock_date > timezone.localdate():
            raise forms.ValidationError(_("The lock date cannot be in the future."))
        if reopens_period(self.instance.lock_date, lock_date) and self._acting_role() != Role.OWNER:
            # Closed books stay closed: reopening them is the owner's decision (and audited).
            raise forms.ValidationError(_("Only the owner can move the lock date back or remove it."))
        return lock_date

    def _acting_role(self):
        return Membership.objects.filter(organization=self.instance, user=self.acting_user).values_list("role", flat=True).first()


def reopens_period(locked_until, new_lock_date) -> bool:
    """Moving the lock date back (or removing it) opens a closed period for changes again."""
    return locked_until is not None and (new_lock_date is None or new_lock_date < locked_until)


def policies_in_use(organization) -> dict[str, tuple[bool, str]]:
    """{policy field: (already applied, why it can't change)}."""
    from apps.assets.models import FixedAsset  # these apps depend on tenants
    from apps.inventory.models import StockMovement

    if organization.pk is None:
        return {}
    return {
        "inventory_cost_formula": (
            StockMovement.objects.filter(organization=organization).exists(),
            _("Fixed: stock has been valued with it. SAK EMKM applies a policy consistently (7.5); a change is applied retrospectively "
              "with an adjustment to retained earnings (7.8-7.9), which your accountant records as a prior-period correction."),
        ),
        "depreciation_method": (
            FixedAsset.objects.filter(organization=organization, is_depreciable=True).exists(),
            _("Fixed: assets in the register use it, and similar assets are depreciated alike (SAK EMKM 7.5). To change how one "
              "asset is depreciated, revise its estimate on the asset's page."),
        ),
    }


class OrganizationCreateForm(BaseForm):
    name = forms.CharField(label=_("Business name"), max_length=200)
    legal_form = forms.ChoiceField(label=_("Legal form"), choices=Organization.LegalForm.choices)


class AddMemberForm(OrganizationFormMixin, BaseForm):
    email = forms.EmailField(label=_("User email"), max_length=EMAIL_MAX_LENGTH)
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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.organization = self.organization  # its NITKU is checked against the organization's NPWP

    def clean_nitku(self):
        nitku = self.cleaned_data["nitku"]
        others = BusinessPlace.objects.for_organization(self.organization).filter(nitku=nitku).exclude(pk=self.instance.pk)
        if others.exists():
            raise forms.ValidationError(_("This NITKU is already registered as another place."))
        return nitku
