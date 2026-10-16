from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _
from django.utils.translation import ngettext
from django.views import View
from django.views.generic import TemplateView

from apps.core.exceptions import DomainError
from apps.tenants.mixins import OrganizationMixin, WriteMixin
from apps.tenants.selectors import first_plan_with

from . import services
from .domain import MAX_UPLOAD_BYTES, RowOutcome, UploadKind
from .forms import UploadForm
from .importers import GROUPS, IMPORTERS, Context, apply, check
from .selectors import uploads_for

PREVIEW_ROWS = 300  # rows shown in a check besides its errors


def kind_of(slug: str) -> str:
    kind = slug.replace("-", "_")
    if kind not in UploadKind.values:
        raise Http404
    return kind


def slug_of(kind: str) -> str:
    return kind.replace("_", "-")


class UploadIndexView(OrganizationMixin, TemplateView):
    template_name = "uploads/index.html"

    def get_context_data(self, **kwargs):
        groups = [
            {"title": title, "intro": intro, "importers": [
                {"importer": i, "slug": slug_of(i.kind), "locked": bool(i.feature) and not self.organization.has_feature(i.feature)}
                for i in IMPORTERS.values() if i.group == key
            ]}
            for key, title, intro in GROUPS
        ]
        return super().get_context_data(groups=groups, uploads=uploads_for(self.organization), **kwargs)


class ImporterMixin:
    def dispatch(self, request, *args, **kwargs):
        self.kind = kind_of(kwargs["slug"])
        self.importer = IMPORTERS[self.kind]
        return super().dispatch(request, *args, **kwargs)

    def locked(self):
        feature = self.importer.feature
        if feature and not self.organization.has_feature(feature):
            return render(self.request, "tenants/upgrade.html", {"feature": feature, "plan": first_plan_with(feature)}, status=403)
        return None

    def options_form(self, data=None):
        form_class = self.importer.options_form
        return form_class(data, organization=self.organization, prefix="options") if form_class else None

    def context(self, options_form) -> Context:
        return Context(self.organization, self.request.user, options_form.cleaned_data if options_form else {}, self.request)

    def page(self, report=None, **context):
        counts = [(value, label, report.count(value)) for value, label in RowOutcome.choices if report.count(value)] if report else []
        return render(self.request, "uploads/upload.html", {
            "importer": self.importer, "slug": slug_of(self.kind), "outcomes": RowOutcome, "report": report, "counts": counts,
            "shown": _shown(report) if report else [], **context,
        })


class UploadTemplateView(ImporterMixin, OrganizationMixin, View):
    def get(self, request, slug):
        response = HttpResponse("﻿" + self.importer.template_csv(), content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="{slug}.csv"'
        return response


class UploadView(ImporterMixin, WriteMixin, View):
    def get(self, request, slug):
        return self.locked() or self.page(form=UploadForm(), options_form=self.options_form())

    def post(self, request, slug):
        if locked := self.locked():
            return locked
        form, options_form = UploadForm(request.POST, request.FILES), self.options_form(request.POST)
        if not form.is_valid() or (options_form is not None and not options_form.is_valid()):
            return self.page(form=form, options_form=options_form)
        upload = form.cleaned_data["file"]
        options = {name: request.POST.get(name, "") for name in (options_form.data if options_form else {}) if name.startswith("options-")}
        pending = services.PendingFile(self.kind, upload.name, upload.read(MAX_UPLOAD_BYTES + 1), options)
        try:
            report = check(self.importer, self.context(options_form), pending)
        except DomainError as exc:
            form.add_error("file", exc.message)
            return self.page(form=form, options_form=options_form)
        token = services.stash_file(organization=self.organization, user=request.user, pending=pending) if report.ok else ""
        return self.page(form=UploadForm(), options_form=options_form, report=report, token=token, file_name=upload.name)


def _shown(report):
    """The errors and the first rows of a check (a long file shows its errors in full)."""
    others = [r for r in report.results if r.outcome != RowOutcome.ERROR][:PREVIEW_ROWS]
    return sorted(report.errors + others, key=lambda r: r.number)


class UploadApplyView(ImporterMixin, WriteMixin, View):
    def post(self, request, slug):
        if locked := self.locked():
            return locked
        token = request.POST.get("token", "")
        pending = services.stashed_file(organization=self.organization, user=request.user, token=token)
        if pending is None or pending.kind != self.kind:
            messages.error(request, _("The checked file has expired: upload it again."))
            return redirect("uploads:upload", slug=slug)
        options_form = self.options_form(pending.options)
        if options_form is not None and not options_form.is_valid():
            messages.error(request, _("The options of the checked file are no longer valid: upload it again."))
            return redirect("uploads:upload", slug=slug)
        try:
            report = apply(self.importer, self.context(options_form), pending)
        except DomainError as exc:
            messages.error(request, exc.message)
            return redirect("uploads:upload", slug=slug)
        if not report.ok:  # the books changed since the check
            return self.page(form=UploadForm(), options_form=options_form, report=report, token="", file_name=pending.name)
        services.discard_file(organization=self.organization, user=request.user, token=token)
        messages.success(request, ngettext(
            "%(file)s applied: %(count)s row recorded.", "%(file)s applied: %(count)s rows recorded.", report.applied,
        ) % {"file": pending.name, "count": report.applied})
        return redirect("uploads:index")
