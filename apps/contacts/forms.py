from django import forms

from apps.core.forms import OrganizationFormMixin

from .models import Contact
from .validators import validate_npwp


class ContactForm(OrganizationFormMixin, forms.ModelForm):
    class Meta:
        model = Contact
        fields = ["kind", "name", "npwp", "email", "phone", "address", "city", "payment_terms_days", "notes", "is_active"]
        widgets = {"address": forms.Textarea(attrs={"rows": 2}), "notes": forms.Textarea(attrs={"rows": 2})}

    def clean_npwp(self):
        npwp = self.cleaned_data["npwp"]
        return validate_npwp(npwp) if npwp else npwp
