"""Small, reusable view helpers shared by every app."""

from django.conf import settings
from django.contrib import messages
from django.shortcuts import redirect
from django.utils.translation import gettext as _
from django.utils.translation import ngettext

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
    ``export_rows(queryset)``: the header, then one row per record (all pages, not just the shown one).

    A download reads the whole filtered list, so it is bounded: at most ``MAX_EXPORT_ROWS`` rows
    (narrow the filter for more) and ``LIST_EXPORTS_PER_HOUR`` downloads per organization."""

    export_name = ""

    def export_rows(self, queryset):  # pragma: no cover - abstract
        raise NotImplementedError

    def get(self, request, *args, **kwargs):
        export_format = choice_param(request.GET, "download", ExportFormat.values)
        if not export_format:
            return super().get(request, *args, **kwargs)
        from apps.accounts.throttle import ThrottleScope, allow  # core is below accounts

        page = request.GET.copy()
        page.pop("download", None)
        back = redirect(f"{request.path}?{page.urlencode()}" if page else request.path)
        queryset = self.get_queryset()
        if (count := queryset.count()) > settings.MAX_EXPORT_ROWS:
            messages.error(request, ngettext(
                "The list has %(count)s row; a download takes at most %(limit)s. Narrow the filter first.",
                "The list has %(count)s rows; a download takes at most %(limit)s. Narrow the filter first.", count,
            ) % {"count": count, "limit": settings.MAX_EXPORT_ROWS})
            return back
        if not allow(ThrottleScope.LIST_EXPORT, str(self.organization.pk), limit=settings.LIST_EXPORTS_PER_HOUR, window_seconds=3600):
            messages.error(request, _("Too many downloads in the last hour. Please try again later."))
            return back
        return download(self.export_name, self.export_rows(queryset), export_format)
