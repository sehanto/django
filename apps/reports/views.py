import csv
import tempfile
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.db import models
from django.db.models import Q
from django.http import FileResponse, HttpResponse
from django.shortcuts import redirect
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.generic import TemplateView

from apps.core.params import choice_param, year_choices, year_param
from apps.core.sanitizers import csv_safe
from apps.invoicing import coretax
from apps.invoicing.models import Invoice, InvoiceKind, ReturnNote
from apps.invoicing.models import CoretaxConnection
from apps.invoicing.selectors import aging_report, vat_summary
from apps.ledger.selectors import account_balances, account_ledger, prior_period_corrections, restated_since
from apps.accounts.throttle import ThrottleScope, allow
from apps.assets.selectors import revisions_in
from apps.audit import services as audit
from apps.audit.domain import AuditAction
from apps.tenants.mixins import ManageMixin, OrganizationMixin

from .exports import archive_filename, write_books_archive
from .forms import AsOfForm, LedgerForm, PeriodForm, end_of_previous_year, one_year_earlier, start_of_year
from .statements import FinancialNotes, build_balance_sheet, build_income_statement, build_trial_balance, number_notes


class ExportFormat(models.TextChoices):
    CSV = "csv", "CSV"


def csv_response(filename: str, rows) -> HttpResponse:
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response.write("﻿")  # BOM so Excel opens UTF-8 correctly
    writer = csv.writer(response, delimiter=";")
    writer.writerows([csv_safe(cell) for cell in row] for row in rows)  # account names etc. are user data
    return response


def income_statement(organization, date_from, date_to):
    """The period with the same period a year earlier as the comparative."""
    previous_from, previous_to = one_year_earlier(date_from), one_year_earlier(date_to)
    report = build_income_statement(
        account_balances(organization, date_from=date_from, date_to=date_to, restated=True), date_from, date_to,
        previous_balances=account_balances(organization, date_from=previous_from, date_to=previous_to, restated=True),
        previous_from=previous_from, previous_to=previous_to,
    )
    report.comparative_restated = restated_since(organization, previous_to)
    return report


def balance_sheet(organization, as_of):
    """Position at ``as_of``, compared with the end of the previous fiscal year."""
    previous_as_of = end_of_previous_year(as_of)

    def before_year(on):
        return account_balances(organization, date_to=start_of_year(on) - timedelta(days=1), restated=True)

    report = build_balance_sheet(
        account_balances(organization, date_to=as_of, restated=True), before_year(as_of), as_of,
        previous_balances_to_date=account_balances(organization, date_to=previous_as_of, restated=True),
        previous_balances_before_year=before_year(previous_as_of), previous_as_of=previous_as_of,
    )
    report.comparative_restated = restated_since(organization, previous_as_of)
    return report


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
        report = income_statement(self.organization, data["date_from"], data["date_to"])
        number_notes(balance_sheet(self.organization, data["date_to"]), report)  # the notes as of the period's end
        return report


class BalanceSheetView(ReportView):
    template_name = "reports/balance_sheet.html"
    form_class = AsOfForm
    csv_name = "balance-sheet.csv"

    def build(self, data):
        report = balance_sheet(self.organization, data["as_of"])
        number_notes(report, income_statement(self.organization, start_of_year(data["as_of"]), data["as_of"]))
        return report


class NotesView(ReportView):
    """Notes for the year to date: what SAK EMKM requires next to the two statements."""

    template_name = "reports/notes.html"
    form_class = AsOfForm

    def build(self, data):
        as_of = data["as_of"]
        notes = FinancialNotes(
            organization=self.organization,
            balance_sheet=balance_sheet(self.organization, as_of),
            income_statement=income_statement(self.organization, start_of_year(as_of), as_of),
            estimate_changes=list(revisions_in(self.organization, start_of_year(as_of), as_of)),
            corrections=prior_period_corrections(self.organization, start_of_year(as_of), as_of),
        )
        number_notes(notes.balance_sheet, notes.income_statement)
        return notes


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
        totals = {
            key: sum(r[key] for r in rows)
            for key in ("output_base", "output", "luxury", "not_collected", "collected", "input_base", "input", "net", "aggregated_base", "aggregated")
        }
        return_months = set(
            ReturnNote.objects.for_organization(self.organization)
            .filter(kind=InvoiceKind.PURCHASE, status=ReturnNote.Status.POSTED, date__year=year).values_list("date__month", flat=True)
        )
        sales_months = set(
            Invoice.objects.for_organization(self.organization)
            .filter(Q(tax_amount__gt=0) | Q(vat_not_collected__gt=0), kind=InvoiceKind.SALES, date__year=year,
                    status__in=[Invoice.Status.ISSUED, Invoice.Status.PARTIAL, Invoice.Status.PAID])
            .values_list("date__month", flat=True)
        ) if self.organization.is_pkp else set()
        return super().get_context_data(
            year=year, years=year_choices(), rows=rows, totals=totals, return_months=return_months, sales_months=sales_months,
            special_months=coretax.special_months(self.organization, year), special_files=coretax.SpecialFile,
            coretax_connected=CoretaxConnection.objects.filter(organization=self.organization, is_active=True).exists(), **kwargs
        )


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
