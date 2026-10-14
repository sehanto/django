from django import forms

from django.utils.translation import gettext_lazy as _

from apps.core.forms import BaseModelForm, DigitsField, OrganizationFormMixin
from apps.regions.forms import RegionFieldsMixin

from .countries import INDONESIA
from .models import Contact
from .validators import nitku_validator, npwp_format_validator


class ContactForm(RegionFieldsMixin, OrganizationFormMixin, BaseModelForm):
    npwp = DigitsField(label=_("tax ID (NPWP)"), max_length=16, required=False, validators=[npwp_format_validator],
                       help_text=_("15 or 16 digits; dots and dashes are removed."))
    nitku = DigitsField(label=_("NITKU (place of business)"), max_length=22, required=False, validators=[nitku_validator],
                        help_text=Contact._meta.get_field("nitku").help_text)

    class Meta:
        model = Contact
        fields = [
            "kind", "name", "npwp", "nitku", "id_type", "id_number", "country", "foreign_tin", "treaty_document", "email", "phone", "address", "village", "payment_terms_days",
            "document_language", "vat_collector", "notes", "is_active",
        ]
        widgets = {"address": forms.Textarea(attrs={"rows": 2}), "notes": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["country"].required = False

    def clean_country(self):
        return (self.cleaned_data.get("country") or INDONESIA).upper()

    def is_domestic(self, cleaned_data) -> bool:
        return cleaned_data.get("country", INDONESIA) == INDONESIA

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("country", INDONESIA) != INDONESIA and cleaned.get("village"):
            self.add_error("village", _("A kelurahan/desa is only for an address in Indonesia; write an address abroad in full."))
        return cleaned
