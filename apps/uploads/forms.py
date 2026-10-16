from django import forms
from django.utils.translation import gettext_lazy as _

from apps.core.exceptions import ValidationFailed
from apps.core.forms import BaseForm

from .domain import MAX_UPLOAD_BYTES, file_format


class UploadForm(BaseForm):
    file = forms.FileField(
        label=_("File"), allow_empty_file=False,
        help_text=_("CSV or XLSX, up to 2 MB. The first row names the columns; their order doesn't matter."),
        widget=forms.ClearableFileInput(attrs={"accept": ".csv,.xlsx,.txt,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}),
    )

    def clean_file(self):
        upload = self.cleaned_data["file"]
        if upload.size > MAX_UPLOAD_BYTES:
            raise forms.ValidationError(_("The file is larger than 2 MB: split it into smaller files."))
        if len(upload.name) > 255:
            raise forms.ValidationError(_("The file name is too long."))
        try:
            file_format(upload.name)
        except ValidationFailed as exc:
            raise forms.ValidationError(exc.message) from None
        return upload
