import csv

from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views import View
from django.views.generic import CreateView, DetailView, FormView, ListView, TemplateView, UpdateView

from apps.core.exceptions import DomainError
from apps.core.params import text_param, year_choices, year_param
from apps.core.sanitizers import csv_safe
from apps.core.views import flash_domain_error
from apps.taxes.domain import Month
from apps.tenants.mixins import ManageMixin, OrganizationMixin, WriteMixin

from . import selectors, services
from .domain import TaxScheme
from .forms import EmployeeForm, PayForm, PayrollRunForm, PayrollSettingsForm, PriorIncomeForm
from .models import Employee, PayrollRun, PayrollSettings, Payslip, PriorIncome


class EmployeeListView(OrganizationMixin, ListView):
    template_name = "payroll/employee_list.html"

    def get_queryset(self):
        return Employee.objects.for_organization(self.organization)


class EmployeeFormMixin(WriteMixin):
    form_class = EmployeeForm
    template_name = "payroll/employee_form.html"
    success_url = reverse_lazy("payroll:employees")

    def get_queryset(self):
        return Employee.objects.for_organization(self.organization)

    def form_valid(self, form):
        form.instance.organization = self.organization
        messages.success(self.request, _("Employee saved."))
        return super().form_valid(form)


class EmployeeCreateView(EmployeeFormMixin, CreateView):
    pass


class EmployeeUpdateView(EmployeeFormMixin, UpdateView):
    def get_context_data(self, **kwargs):
        return super().get_context_data(
            prior_incomes=self.object.prior_incomes.all(),
            prior_form=PriorIncomeForm(initial={"year": timezone.localdate().year}), **kwargs,
        )


class PriorIncomeCreateView(WriteMixin, View):
    """An earlier employer's 1721-A1 of the year, handed in by the employee."""

    def post(self, request, pk):
        employee = get_object_or_404(Employee.objects.for_organization(self.organization), pk=pk)
        form = PriorIncomeForm(request.POST)
        if not form.is_valid():
            messages.error(request, " ".join(e for errors in form.errors.values() for e in errors))
            return redirect("payroll:employee_update", employee.pk)
        try:
            services.add_prior_income(employee=employee, **form.cleaned_data)
            messages.success(request, _("Income from the previous employer recorded."))
        except DomainError as exc:
            flash_domain_error(request, exc)
        return redirect("payroll:employee_update", employee.pk)


class PriorIncomeDeleteView(WriteMixin, View):
    def post(self, request, pk):
        prior = get_object_or_404(PriorIncome.objects.for_organization(self.organization), pk=pk)
        try:
            services.remove_prior_income(prior=prior)
            messages.success(request, _("Income from the previous employer removed."))
        except DomainError as exc:
            flash_domain_error(request, exc)
        return redirect("payroll:employee_update", prior.employee_id)


class PayrollView(OrganizationMixin, TemplateView):
    """The year's payroll by month."""

    template_name = "payroll/index.html"

    def get_context_data(self, **kwargs):
        year = year_param(self.request.GET)
        runs = {r.month: r for r in PayrollRun.objects.for_organization(self.organization).filter(year=year, status=PayrollRun.Status.POSTED)}
        return super().get_context_data(
            year=year, years=year_choices(), months=[(m, runs.get(m)) for m in Month],
            has_employees=Employee.objects.for_organization(self.organization).filter(is_active=True).exists(), **kwargs,
        )


def _employees_in(organization, year, month):
    start, end = timezone.datetime(year, month, 1).date(), services.month_end(year, month)
    return [
        e for e in Employee.objects.for_organization(organization).filter(start_date__lte=end)
        if (e.end_date is None or e.end_date >= start) and (e.is_active or e.end_date)
        or e.is_active and e.left_before(year, month)  # severance in instalments
    ]


class PayrollRunCreateView(WriteMixin, FormView):
    template_name = "payroll/run_form.html"
    form_class = PayrollRunForm

    def dispatch(self, request, *args, **kwargs):
        if not 1 <= kwargs["month"] <= 12:
            raise Http404
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        year, month = self.kwargs["year"], self.kwargs["month"]
        return {**super().get_form_kwargs(), "employees": _employees_in(self.organization, year, month), "year": year, "month": month}

    def get_context_data(self, **kwargs):
        return super().get_context_data(year=self.kwargs["year"], month=Month(self.kwargs["month"]), **kwargs)

    def form_valid(self, form):
        try:
            run = services.run_payroll(
                organization=self.organization, year=self.kwargs["year"], month=self.kwargs["month"], pays=form.pays(),
                created_by=self.request.user,
            )
        except DomainError as exc:
            form.add_error(None, str(exc))
            return self.form_invalid(form)
        messages.success(self.request, _("Payroll recorded and posted to the ledger."))
        return redirect("payroll:run", run.pk)


class PayrollRunView(OrganizationMixin, DetailView):
    template_name = "payroll/run_detail.html"
    context_object_name = "run"

    def get_queryset(self):
        return PayrollRun.objects.for_organization(self.organization).select_related("journal_entry", "payment_entry", "bpjs_entry")

    def get_context_data(self, **kwargs):
        return super().get_context_data(
            payslips=self.object.payslips.select_related("employee"), schemes=TaxScheme,
            pay_form=PayForm(organization=self.organization, initial={"paid_on": timezone.localdate()}), **kwargs,
        )

    def render_to_response(self, context, **response_kwargs):
        if self.kwargs.get("csv"):
            return self.csv(context)
        return super().render_to_response(context, **response_kwargs)

    def csv(self, context):
        run = self.object
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="pph21-{run.year}-{run.month:02d}.csv"'
        response.write("﻿")
        writer = csv.writer(response, delimiter=";")
        writer.writerow([
            _("Name"), _("NIK"), _("Passport no."), _("Country"), _("NPWP"), _("PTKP status"), _("TER category"), _("Tax computed as"),
            _("Days worked"), _("Tax allowance"), _("Benefits in kind"), _("Gross income"), _("Rate (%)"),
            # xgettext:no-python-format
            _("Without NPWP (20% higher)"),
            _("Tax withheld"), _("Severance pay"), _("Final PPh 21 on severance"), _("Bukti potong no."),
        ])
        for p in context["payslips"]:
            e = p.employee
            writer.writerow([csv_safe(cell) for cell in (
                e.name, e.nik, e.passport_number, e.nationality, e.npwp, p.ptkp_status, p.ter_category, p.get_tax_scheme_display(),
                p.days_worked or "", p.tax_allowance, p.benefits_in_kind, p.taxable_gross,
                p.ter_rate if p.ter_rate is not None else (p.pph26_rate if p.pph26_rate is not None else ""), _("yes") if p.without_tin else "",
                p.pph21, p.severance or "", p.severance_tax or "", p.withholding_slip,
            )])
        return response


class AnnualView(OrganizationMixin, TemplateView):
    """The year's 1721-A1 statements of permanent employees (give them out by the end of January)."""

    template_name = "payroll/annual.html"

    def get_context_data(self, **kwargs):
        year = self.kwargs["year"]
        for name in ("year", "csv"):  # the URL's, replaced below
            kwargs.pop(name, None)
        return super().get_context_data(year=year, statements=selectors.annual_statements(self.organization, year), **kwargs)

    def render_to_response(self, context, **response_kwargs):
        if self.kwargs.get("csv"):
            return self.csv(context)
        return super().render_to_response(context, **response_kwargs)

    def csv(self, context):
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="1721-a1-{context["year"]}.csv"'
        response.write("\ufeff")
        writer = csv.writer(response, delimiter=";")
        writer.writerow([
            _("Name"), _("NIK"), _("Passport no."), _("Country"), _("NPWP"), _("PTKP status"), _("First month"), _("Last month"),
            _("Gross income"), _("Occupational costs"), _("Pension contributions"), _("Zakat"), _("Net income"),
            _("Net income from earlier periods"), _("Net income for the year (annualised)"), _("PTKP"), _("Taxable income"),
            _("PPh 21 for the year"), _("PPh 21 withheld in earlier periods"), _("PPh 21 due"), _("PPh 21 withheld"), _("Bukti potong no."),
        ])
        for st in context["statements"]:
            if not st.is_final:
                continue
            e, a = st.employee, st.annual
            writer.writerow([csv_safe(cell) for cell in (
                e.name, e.nik, e.passport_number, e.nationality, e.npwp, st.final_slip.ptkp_status, st.first_month, st.last_month,
                st.gross, a.occupational_costs, a.contributions, a.zakat, a.net, a.prior_net, a.annual_net, a.ptkp, a.taxable, st.tax,
                st.prior_tax, st.due, st.withheld,
                st.final_slip.withholding_slip,
            )])
        return response


class AnnualStatementView(OrganizationMixin, TemplateView):
    """Form 1721-A1 of one employee, to print."""

    template_name = "payroll/a1.html"

    def get_context_data(self, **kwargs):
        employee = get_object_or_404(Employee.objects.for_organization(self.organization), pk=self.kwargs["pk"])
        statement = selectors.annual_statement(employee, self.kwargs["year"])
        if statement is None:
            raise Http404
        return super().get_context_data(st=statement, **kwargs)


class PayslipView(OrganizationMixin, DetailView):
    template_name = "payroll/payslip.html"
    context_object_name = "slip"

    def get_queryset(self):
        return Payslip.objects.filter(run__organization=self.organization).select_related("employee", "run")


class PayrollActionView(WriteMixin, View):
    ACTIONS = ("pay", "bpjs", "void")

    def post(self, request, pk, action):
        if action not in self.ACTIONS:
            raise Http404
        run = get_object_or_404(PayrollRun.objects.for_organization(self.organization), pk=pk)
        try:
            if action == "void":
                services.void_payroll(run=run, on=timezone.localdate(), voided_by=request.user)
                messages.success(request, _("Payroll voided (journal entry reversed)."))
            else:
                form = PayForm(request.POST, organization=self.organization)
                if not form.is_valid():
                    messages.error(request, _("The payment details are incomplete."))
                    return redirect("payroll:run", run.pk)
                pay = services.pay_salaries if action == "pay" else services.pay_bpjs
                pay(run=run, created_by=request.user, **form.cleaned_data)
                messages.success(request, _("Payment recorded and posted to the ledger."))
        except DomainError as exc:
            flash_domain_error(request, exc)
        return redirect("payroll:run", run.pk)


class PayslipSlipNumberView(WriteMixin, View):
    def post(self, request, pk):
        slip = get_object_or_404(Payslip.objects.filter(run__organization=self.organization), pk=pk)
        try:
            services.record_slip_number(payslip=slip, number=text_param(request.POST, "number"))
            messages.success(request, _("Bukti potong number recorded."))
        except DomainError as exc:
            flash_domain_error(request, exc)
        return redirect("payroll:run", slip.run_id)


class PayrollSettingsView(ManageMixin, FormView):
    template_name = "payroll/settings.html"
    form_class = PayrollSettingsForm

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        try:
            kwargs["instance"] = services.settings_for(self.organization)
        except DomainError:
            kwargs["instance"] = PayrollSettings(organization=self.organization)
        return kwargs

    def form_valid(self, form):
        try:
            services.save_settings(organization=self.organization, **form.cleaned_data)
        except DomainError as exc:
            form.add_error(None, str(exc))
            return self.form_invalid(form)
        messages.success(self.request, _("Payroll settings saved."))
        return redirect(reverse("payroll:index"))
