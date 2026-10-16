"""Small, reusable view helpers shared by every app."""

from django.contrib import messages

from .exceptions import DomainError
from .params import choice_param
from .tables import ExportFormat, download


class DomainErrorFormMixin:
    """Turn a ``DomainError`` raised by a service into a form error.

    Subclasses implement ``perform(form)`` which calls the service layer and
    returns the redirect response.
    """

    def form_valid(self, form):
        try:
            return self.perform(form)
        except DomainError as exc:
            form.add_error(None, exc.message)
            return self.form_invalid(form)

    def perform(self, form):  # pragma: no cover - abstract
        raise NotImplementedError


def flash_domain_error(request, exc: DomainError) -> None:
    messages.error(request, exc.message)


class ExportMixin:
    """A list page whose rows, as filtered, download as CSV or Excel (``?download=csv`` or ``xlsx``;
    ``partials/export_links.html`` shows the buttons). Subclasses give ``export_name`` and
    ``export_rows(queryset)``: the header, then one row per record (all pages, not just the shown one)."""

    export_name = ""

    def export_rows(self, queryset):  # pragma: no cover - abstract
        raise NotImplementedError

    def get(self, request, *args, **kwargs):
        export_format = choice_param(request.GET, "download", ExportFormat.values)
        if export_format:
            return download(self.export_name, self.export_rows(self.get_queryset()), export_format)
        return super().get(request, *args, **kwargs)
