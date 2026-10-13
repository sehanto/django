from django import forms

from django.utils.translation import gettext_lazy as _

from apps.core.forms import BaseModelForm, DigitsField, OrganizationFormMixin

from .models import Contact
from .validators import npwp_format_validator


class ContactForm(OrganizationFormMixin, BaseModelForm):
    npwp = DigitsField(label=_("tax ID (NPWP)"), max_length=16, required=False, validators=[npwp_format_validator],
                       help_text=_("15 or 16 digits; dots and dashes are removed."))

    class Meta:
        model = Contact
        fields = [
            "kind", "name", "npwp", "email", "phone", "address", "city", "payment_terms_days",
            "document_language", "vat_collector", "notes", "is_active",
        ]
        widgets = {"address": forms.Textarea(attrs={"rows": 2}), "notes": forms.Textarea(attrs={"rows": 2})}
