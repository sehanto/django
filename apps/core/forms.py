import re

from django import forms
from django.http import QueryDict
from django.utils.translation import gettext_lazy as _

from .sanitizers import sanitize_text
from .validators import EARLIEST_BUSINESS_DATE, latest_business_date


class SanitizedFormMixin:
    """Normalize every text input before any validation runs.

    Applies to all ``CharField``-based fields (including email/URL fields) except
    passwords, which must be used exactly as typed. Textareas keep line breaks.
    Re-displayed forms show the normalized value, i.e. exactly what will be stored.
    """

    def full_clean(self):
        if self.is_bound:
            self.data = self._sanitized_data()
        super().full_clean()

    def _sanitized_data(self):
        data = self.data.copy()
        for name, field in self.fields.items():
            if not isinstance(field, forms.CharField) or isinstance(field.widget, forms.PasswordInput):
                continue
            key = self.add_prefix(name)
            if key not in data:
                continue
            multiline = isinstance(field.widget, forms.Textarea)
            if isinstance(data, QueryDict):
                data.setlist(key, [sanitize_text(v, multiline=multiline) for v in data.getlist(key)])
            else:
                data[key] = sanitize_text(str(data[key]), multiline=multiline)
        return data


class DigitsField(forms.CharField):
    """Numeric identifier typed with separators (``01.234.567.8-901.000``), stored as digits.

    Separators are removed in ``to_python``, i.e. before length and format checks.
    """

    SEPARATORS = re.compile(r"[\s.\-/]")

    def to_python(self, value):
        return self.SEPARATORS.sub("", super().to_python(value))


class BaseForm(SanitizedFormMixin, forms.Form):
    """Base for every plain form in the project."""


class BaseModelForm(SanitizedFormMixin, forms.ModelForm):
    """Base for every model form in the project."""


class OrganizationFormMixin:
    """Accept the current ``organization`` so choices can be tenant-scoped."""

    def __init__(self, *args, organization=None, **kwargs):
        self.organization = organization
        super().__init__(*args, **kwargs)


class DateInput(forms.DateInput):
    input_type = "date"

    def __init__(self, attrs=None):
        super().__init__(attrs=attrs, format="%Y-%m-%d")


class BusinessDateField(forms.DateField):
    """A date limited to a plausible business range (also shown as min/max in the picker)."""

    widget = DateInput

    def validate(self, value):
        super().validate(value)
        if value and not EARLIEST_BUSINESS_DATE <= value <= latest_business_date():
            raise forms.ValidationError(
                _("Enter a date between %(start)s and %(end)s."),
                code="out_of_range",
                params={
                    "start": EARLIEST_BUSINESS_DATE.strftime("%d-%m-%Y"),
                    "end": latest_business_date().strftime("%d-%m-%Y"),
                },
            )

    def widget_attrs(self, widget):
        attrs = super().widget_attrs(widget)
        attrs.update(min=EARLIEST_BUSINESS_DATE.isoformat(), max=latest_business_date().isoformat())
        return attrs
