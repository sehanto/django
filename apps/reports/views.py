import csv
import tempfile
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.db import models
from django.http import FileResponse, HttpResponse
from django.shortcuts import redirect
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.generic import TemplateView

from apps.core.params import choice_param, year_choices, year_param
from apps.core.sanitizers import csv_safe
from apps.invoicing.models import InvoiceKind
from apps.invoicing.selectors import aging_report, vat_summary
from apps.ledger.selectors import account_balances, account_ledger
from apps.accounts.throttle import ThrottleScope, allow
from apps.audit import services as audit
from apps.audit.domain import AuditAction
from apps.tenants.mixins import ManageMixin, OrganizationMixin

from .exports import archive_filename, write_books_archive
from .forms import AsOfForm, LedgerForm, PeriodForm, start_of_year
from .statements import build_balance_sheet, build_income_statement, build_trial_balance


class ExportFormat(models.TextChoices):
    CSV = "csv", "CSV"


def csv_response(filename: str, rows) -> HttpResponse:
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response.write("﻿")  # BOM so Excel opens UTF-8 correctly
    writer = csv.writer(response, delimiter=";")
    writer.writerows([csv_safe(cell) for cell in row] for row in rows)  # account names etc. are user data
    return response


class ReportView(OrganizationMixin, TemplateView):
    """Base: validate a filter form, build a report, render HTML or CSV."""

    form_class = None
    csv_name = None

    def get_form(self):
        return self.form_class(self.request.GET or None)

    def build(self, data):  # pragma: no cover - abstract
        raise NotImplementedError

    def get(self, request, *args, **kwargs):
        form = self.get_form()
        report = self.build(form.cleaned_data) if form.is_valid() else None
        if report is not None and self.csv_name and choice_param(request.GET, "download", ExportFormat.values) == ExportFormat.CSV:
            return csv_response(self.csv_name, report.csv_rows())
        return self.render_to_response(self.get_context_data(form=form, report=report, export_formats=ExportFormat))


class ReportIndexView(OrganizationMixin, TemplateView):
    template_name = "reports/index.html"


class TrialBalanceView(ReportView):
    template_name = "reports/trial_balance.html"
    form_class = AsOfForm
    csv_name = "trial-balance.csv"

    def build(self, data):
        return build_trial_balance(account_balances(self.organization, date_to=data["as_of"]), data["as_of"])


class IncomeStatementView(ReportView):
    template_name = "reports/income_statement.html"
    form_class = PeriodForm
    csv_name = "income-statement.csv"

    def build(self, data):
        balances = account_balances(self.organization, date_from=data["date_from"], date_to=data["date_to"])
        return build_income_statement(balances, data["date_from"], data["date_to"])


class BalanceSheetView(ReportView):
    template_name = "reports/balance_sheet.html"
    form_class = AsOfForm
    csv_name = "balance-sheet.csv"

    def build(self, data):
        as_of = data["as_of"]
        year_start = start_of_year(as_of)
        return build_balance_sheet(
            account_balances(self.organization, date_to=as_of),
            account_balances(self.organization, date_to=year_start - timedelta(days=1)),
            as_of,
        )


class GeneralLedgerView(ReportView):
    template_name = "reports/general_ledger.html"

    def get_form(self):
        return LedgerForm(self.request.GET or None, organization=self.organization)

    def build(self, data):
        if not data.get("account"):
            return None
        return account_ledger(self.organization, data["account"], date_from=data["date_from"], date_to=data["date_to"])


class AgingView(OrganizationMixin, TemplateView):
    template_name = "reports/aging.html"

    def get_context_data(self, **kwargs):
        kind = self.kwargs["kind"]
        return super().get_context_data(
            report=aging_report(self.organization, kind),
            title=_("Receivables aging") if kind == InvoiceKind.SALES else _("Payables aging"),
            **kwargs,
        )


class VatReportView(OrganizationMixin, TemplateView):
    template_name = "reports/vat.html"

    def get_context_data(self, **kwargs):
        year = year_param(self.request.GET)
        rows = vat_summary(self.organization, year)
        totals = {key: sum(r[key] for r in rows) for key in ("output_base", "output", "input_base", "input", "net")}
        return super().get_context_data(year=year, years=year_choices(), rows=rows, totals=totals, **kwargs)


class ExportBooksView(ManageMixin, TemplateView):
    """Owners and admins download the organization's complete books (ZIP of CSV files)."""

    template_name = "reports/export_books.html"
    SPOOL_BYTES = 16 * 1024 * 1024  # larger archives go to a temporary file

    def post(self, request):
        organization = self.organization
        if not allow(ThrottleScope.BOOKS_EXPORT, str(organization.pk), limit=settings.BOOKS_EXPORTS_PER_HOUR, window_seconds=3600):
            messages.error(request, _("Too many exports in the last hour. Please try again later."))
            return redirect("reports:export_books")
        archive = tempfile.SpooledTemporaryFile(max_size=self.SPOOL_BYTES)
        write_books_archive(organization, archive, exported_by=request.user)
        archive.seek(0)
        audit.record(AuditAction.BOOKS_EXPORTED, request=request, organization=organization, via="web")
        return FileResponse(
            archive, as_attachment=True, content_type="application/zip",
            filename=archive_filename(organization, timezone.localdate()),
        )
