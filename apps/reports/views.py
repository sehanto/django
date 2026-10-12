import csv
from datetime import timedelta

from django.http import HttpResponse
from django.utils import timezone
from django.views.generic import TemplateView

from apps.invoicing.models import InvoiceKind
from apps.invoicing.selectors import aging_report, vat_summary
from apps.ledger.selectors import account_balances, account_ledger
from apps.tenants.mixins import OrganizationMixin

from .forms import AsOfForm, LedgerForm, PeriodForm, start_of_year
from .statements import build_balance_sheet, build_income_statement, build_trial_balance


def csv_response(filename: str, rows) -> HttpResponse:
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response.write("﻿")  # BOM so Excel opens UTF-8 correctly
    writer = csv.writer(response, delimiter=";")
    writer.writerows(rows)
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
        if report is not None and self.csv_name and request.GET.get("unduh") == "csv":
            return csv_response(self.csv_name, report.csv_rows())
        return self.render_to_response(self.get_context_data(form=form, report=report))


class ReportIndexView(OrganizationMixin, TemplateView):
    template_name = "reports/index.html"


class TrialBalanceView(ReportView):
    template_name = "reports/trial_balance.html"
    form_class = AsOfForm
    csv_name = "neraca-saldo.csv"

    def build(self, data):
        return build_trial_balance(account_balances(self.organization, date_to=data["as_of"]), data["as_of"])


class IncomeStatementView(ReportView):
    template_name = "reports/income_statement.html"
    form_class = PeriodForm
    csv_name = "laba-rugi.csv"

    def build(self, data):
        balances = account_balances(self.organization, date_from=data["date_from"], date_to=data["date_to"])
        return build_income_statement(balances, data["date_from"], data["date_to"])


class BalanceSheetView(ReportView):
    template_name = "reports/balance_sheet.html"
    form_class = AsOfForm
    csv_name = "posisi-keuangan.csv"

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
            title="Umur Piutang" if kind == InvoiceKind.SALES else "Umur Utang",
            **kwargs,
        )


class VatReportView(OrganizationMixin, TemplateView):
    template_name = "reports/vat.html"

    def get_context_data(self, **kwargs):
        try:
            year = int(self.request.GET.get("tahun", ""))
        except ValueError:
            year = timezone.localdate().year
        rows = vat_summary(self.organization, year)
        totals = {key: sum(r[key] for r in rows) for key in ("output_base", "output", "input_base", "input", "net")}
        current = timezone.localdate().year
        return super().get_context_data(
            year=year, years=range(current, current - 6, -1), rows=rows, totals=totals, **kwargs
        )
