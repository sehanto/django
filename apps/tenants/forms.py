from django import forms

from apps.contacts.validators import validate_npwp
from apps.core.forms import DateInput, OrganizationFormMixin

from .models import Organization
from .roles import Role


class OrganizationSettingsForm(OrganizationFormMixin, forms.ModelForm):
    class Meta:
        model = Organization
        fields = [
            "name", "legal_form", "npwp", "nib", "address", "city", "phone", "email",
            "is_pkp", "uses_pph_final", "lock_date",
        ]
        widgets = {"address": forms.Textarea(attrs={"rows": 2}), "lock_date": DateInput()}

    def clean_npwp(self):
        npwp = self.cleaned_data["npwp"]
        return validate_npwp(npwp) if npwp else npwp


class OrganizationCreateForm(forms.Form):
    name = forms.CharField(label="Nama usaha", max_length=200)
    legal_form = forms.ChoiceField(label="Bentuk usaha", choices=Organization.LegalForm.choices)


class AddMemberForm(OrganizationFormMixin, forms.Form):
    email = forms.EmailField(label="Email pengguna")
    role = forms.ChoiceField(
        label="Peran",
        choices=[(r.value, r.label) for r in Role if r != Role.OWNER],
        initial=Role.ACCOUNTANT,
    )
