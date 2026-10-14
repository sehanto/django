import csv
from dataclasses import dataclass
from decimal import Decimal

from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views import View
from django.views.generic import CreateView, ListView, TemplateView, UpdateView

from apps.core.exceptions import DomainError
from apps.core.money import ZERO
from apps.core.sanitizers import csv_safe
from apps.core.params import int_param, text_param, year_choices, year_param
from apps.core.views import flash_domain_error
from apps.tenants.mixins import OrganizationMixin, WriteMixin

from . import ebupot, services
from .domain import MSME_TURNOVER_LIMIT, PPH_FINAL_RATE, Month, PayerWithholding, PphFinalMonth
from .forms import PphFinalPaymentForm, TaxObjectForm, TaxRateForm, WithholdingDepositForm
from .models import PphFinalAccrual, PphFinalPayment, WithholdingDeposit
from .selectors import (
    pph_final_accruals, pph_final_payments, pph_final_schedule, pph_final_withheld, tax_rates_for, withholding_deposits,
    withholding_due, withholdings,
)


class TaxRateListView(OrganizationMixin, ListView):
    template_name = "taxes/taxrate_list.html"

    def get_queryset(self):
        return tax_rates_for(self.organization)


class TaxRateFormMixin(WriteMixin):
    form_class = TaxRateForm
    template_name = "taxes/taxrate_form.html"
    success_url = reverse_lazy("taxes:rates")

    def get_queryset(self):
        return tax_rates_for(self.organization)

    def form_valid(self, form):
        form.instance.organization = self.organization
        messages.success(self.request, _("Tax rate saved."))
        return super().form_valid(form)


class TaxRateCreateView(TaxRateFormMixin, CreateView):
    pass


class TaxRateUpdateView(TaxRateFormMixin, UpdateView):
    pass


def _selected_year(request) -> int:
    return year_param(request.POST if request.method == "POST" else request.GET)


@dataclass(frozen=True)
class PphFinalRow:
    period: PphFinalMonth
    accrual: PphFinalAccrual | None
    payment: PphFinalPayment | None
    has_ended: bool
    withheld: Decimal = ZERO  # by buyers, from the month's receipts

    @property
    def covered_by_withholding(self) -> bool:
        due = self.accrual.tax_amount if self.accrual else self.period.tax
        return self.payment is None and bool(self.withheld) and self.withheld >= due

    @property
    def accrual_outdated(self) -> bool:
        """Turnover of the month changed after its expense was recorded."""
        return self.accrual is not None and self.accrual.tax_amount != self.period.tax


class PphFinalView(OrganizationMixin, TemplateView):
    template_name = "taxes/pph_final.html"

    def get_context_data(self, **kwargs):
        year = _selected_year(self.request)
        schedule = pph_final_schedule(self.organization, year)
        payments = pph_final_payments(self.organization, year)
        accruals = pph_final_accruals(self.organization, year)
        withheld = pph_final_withheld(self.organization, year)
        today = timezone.localdate()
        annual_turnover = sum(m.gross_turnover for m in schedule)
        return super().get_context_data(
            year=year,
            years=year_choices(),
            rows=[
                PphFinalRow(
                    m, accruals.get(m.month), payments.get(m.month), services.month_end(year, m.month) <= today,
                    withheld.get(m.month, ZERO),
                )
                for m in schedule
            ],
            any_withheld=bool(withheld),
            total_turnover=annual_turnover,
            total_tax=sum(m.tax for m in schedule),
            over_limit=annual_turnover > MSME_TURNOVER_LIMIT,
            limit=MSME_TURNOVER_LIMIT,
            rate_percent=PPH_FINAL_RATE * 100,
            payment_form=PphFinalPaymentForm(organization=self.organization, initial={"paid_on": timezone.localdate()}),
            **kwargs,
        )


class PphFinalPayView(WriteMixin, View):
    def post(self, request):
        year = _selected_year(request)
        form = PphFinalPaymentForm(request.POST, organization=self.organization)
        if not form.is_valid():
            messages.error(request, _("The payment details are incomplete."))
        else:
            try:
                services.record_pph_final_payment(
                    organization=self.organization, year=year, created_by=request.user, **form.cleaned_data
                )
                messages.success(request, _("PPh Final payment recorded and posted to the ledger."))
            except DomainError as exc:
                flash_domain_error(request, exc)
        return redirect(f"{reverse('taxes:pph_final')}?year={year}")


class PphFinalCancelView(WriteMixin, View):
    def post(self, request, pk):
        payment = get_object_or_404(
            PphFinalPayment.objects.for_organization(self.organization).filter(status=PphFinalPayment.Status.POSTED), pk=pk
        )
        year = payment.year
        try:
            services.cancel_pph_final_payment(payment=payment, on=timezone.localdate(), created_by=request.user)
            messages.success(request, _("PPh Final payment cancelled (journal entry reversed)."))
        except DomainError as exc:
            flash_domain_error(request, exc)
        return redirect(f"{reverse('taxes:pph_final')}?year={year}")


class PphFinalAccrueView(WriteMixin, View):
    def post(self, request):
        year = _selected_year(request)
        month = int_param(request.POST, "month", minimum=Month.JANUARY, maximum=Month.DECEMBER)
        if month is None:
            messages.error(request, _("Choose a month."))
        else:
            try:
                services.accrue_pph_final(organization=self.organization, year=year, month=Month(month), created_by=request.user)
                messages.success(request, _("PPh Final expense recorded and posted to the ledger."))
            except DomainError as exc:
                flash_domain_error(request, exc)
        return redirect(f"{reverse('taxes:pph_final')}?year={year}")


class PphFinalAccrualCancelView(WriteMixin, View):
    def post(self, request, pk):
        accrual = get_object_or_404(
            PphFinalAccrual.objects.for_organization(self.organization).filter(status=PphFinalAccrual.Status.POSTED), pk=pk
        )
        try:
            services.cancel_pph_final_accrual(accrual=accrual, on=timezone.localdate(), created_by=request.user)
            messages.success(request, _("PPh Final expense cancelled (journal entry reversed)."))
        except DomainError as exc:
            flash_domain_error(request, exc)
        return redirect(f"{reverse('taxes:pph_final')}?year={accrual.year}")


# --- Income tax withheld from suppliers (PPh 23 / 4(2)) -------------------------------


@dataclass(frozen=True)
class WithholdingRow:
    month: Month
    kind: PayerWithholding
    due: Decimal
    deposit: WithholdingDeposit | None
    detail_url: str = ""  # the month's bukti potong: supplier payments, or the payroll run


class WithholdingView(OrganizationMixin, TemplateView):
    template_name = "taxes/withholding.html"

    def get_context_data(self, **kwargs):
        year = _selected_year(self.request)
        due = withholding_due(self.organization, year)
        deposits = withholding_deposits(self.organization, year)
        rows = [  # each links to the month's bukti potong and eBupot files
            WithholdingRow(month, kind, due.get((kind, month), ZERO), deposits.get((kind, month)),
                           reverse("taxes:withholding_month", args=[year, month]))
            for month in Month for kind in PayerWithholding
            if due.get((kind, month)) or (kind, month) in deposits
        ]
        return super().get_context_data(
            year=year, years=year_choices(), rows=rows, today=timezone.localdate(),
            deposit_form=WithholdingDepositForm(organization=self.organization, initial={"paid_on": timezone.localdate()}),
            **kwargs,
        )


class WithholdingMonthView(OrganizationMixin, TemplateView):
    """The month's withholdings: one bukti potong each (create them in Coretax eBupot)."""

    template_name = "taxes/withholding_month.html"

    def get_context_data(self, **kwargs):
        year, month = self.kwargs["year"], self.kwargs["month"]
        if not 1 <= month <= 12:
            raise Http404
        for name in ("year", "month", "csv"):  # the URL's, replaced below
            kwargs.pop(name, None)
        from apps.payroll.models import PayrollRun  # payroll depends on taxes

        run = PayrollRun.objects.for_organization(self.organization).filter(year=year, month=month, status=PayrollRun.Status.POSTED).first()
        from apps.invoicing.coretax_sync import connector_for

        return super().get_context_data(
            year=year, month=Month(month), payments=withholdings(self.organization, year, month), run=run,
            batches=list(ebupot.month_batches(self.organization, year, month).values()),
            can_send=connector_for(self.organization) is not None, object_form=TaxObjectForm(),
            **kwargs,
        )

    def render_to_response(self, context, **response_kwargs):
        if self.kwargs.get("csv"):
            return self.csv(context)
        return super().render_to_response(context, **response_kwargs)

    def csv(self, context):
        period = f"{context['year']}-{context['month']:02d}"
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="bukti-potong-{period}.csv"'
        response.write("﻿")  # BOM so Excel opens UTF-8 correctly
        writer = csv.writer(response, delimiter=";")
        writer.writerow([
            _("Date"), _("Payment"), _("Bill"), _("Supplier"), _("NPWP"), _("NIK/ID"), _("Income tax"),
            _("Tax base"), _("Rate (%)"), _("Tax withheld"), _("Bukti potong no."),
        ])
        for p in context["payments"]:
            invoice = p.invoice
            writer.writerow([csv_safe(cell) for cell in (
                p.date.isoformat(), p.number, invoice.number, invoice.party_name, invoice.party_tin, invoice.party_id_number,
                p.get_income_tax_kind_display(), p.income_tax_base,
                p.income_tax_rate if p.income_tax_rate is not None else _("50% × Art. 17"), p.income_tax_withheld, p.withholding_slip,
            )])
        return response


def _ebupot_type(kind: str) -> str:
    if kind not in ebupot.ROOTS:
        raise Http404
    return kind


class EbupotXmlView(OrganizationMixin, View):
    """A month's eBupot import file, to upload in Coretax (or send through the connection)."""

    def get(self, request, year, month, kind):
        document_type = _ebupot_type(kind)
        if not 1 <= month <= 12:
            raise Http404
        batch = ebupot.month_batches(self.organization, year, month).get(document_type)
        if batch is None or not batch.records:
            raise Http404
        response = HttpResponse(batch.xml(self.organization), content_type="application/xml")
        response["Content-Disposition"] = f'attachment; filename="{ebupot.file_name(self.organization, document_type, year, month)}"'
        return response


class EbupotSendView(WriteMixin, View):
    def post(self, request, year, month, kind):
        from apps.invoicing import coretax_sync

        document_type = _ebupot_type(kind)
        try:
            submission = coretax_sync.submit_withholdings(
                organization=self.organization, year=year, month=month, document_type=document_type, created_by=request.user,
            )
            messages.success(request, _("Sent to Coretax: %(status)s.") % {"status": submission.get_status_display()})
        except DomainError as exc:
            flash_domain_error(request, exc)
        return redirect("taxes:withholding_month", year, month)


class PaymentTaxObjectView(WriteMixin, View):
    """Set the tax object code of a supplier payment recorded without one."""

    def post(self, request, pk):
        from apps.invoicing.models import Payment
        from apps.invoicing.services import record_tax_object

        payment = get_object_or_404(Payment.objects.for_organization(self.organization), pk=pk)
        try:
            record_tax_object(payment=payment, code=text_param(request.POST, "code"))
            messages.success(request, _("Tax object code recorded."))
        except DomainError as exc:
            flash_domain_error(request, exc)
        return redirect("taxes:withholding_month", payment.date.year, payment.date.month)


class WithholdingDepositView(WriteMixin, View):
    def post(self, request):
        year = _selected_year(request)
        form = WithholdingDepositForm(request.POST, organization=self.organization)
        if not form.is_valid():
            messages.error(request, _("The payment details are incomplete."))
        else:
            try:
                services.record_withholding_deposit(organization=self.organization, year=year, created_by=request.user, **form.cleaned_data)
                messages.success(request, _("Deposit recorded and posted to the ledger."))
            except DomainError as exc:
                flash_domain_error(request, exc)
        return redirect(f"{reverse('taxes:withholding')}?year={year}")


class WithholdingDepositCancelView(WriteMixin, View):
    def post(self, request, pk):
        deposit = get_object_or_404(
            WithholdingDeposit.objects.for_organization(self.organization).filter(status=WithholdingDeposit.Status.POSTED), pk=pk
        )
        try:
            services.cancel_withholding_deposit(deposit=deposit, on=timezone.localdate(), created_by=request.user)
            messages.success(request, _("Deposit cancelled (journal entry reversed)."))
        except DomainError as exc:
            flash_domain_error(request, exc)
        return redirect(f"{reverse('taxes:withholding')}?year={deposit.year}")
