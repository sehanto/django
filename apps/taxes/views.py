from dataclasses import dataclass

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views import View
from django.views.generic import CreateView, ListView, TemplateView, UpdateView

from apps.core.exceptions import DomainError
from apps.core.params import int_param, year_choices, year_param
from apps.core.views import flash_domain_error
from apps.tenants.mixins import OrganizationMixin, WriteMixin

from . import services
from .domain import MSME_TURNOVER_LIMIT, PPH_FINAL_RATE, Month, PphFinalMonth
from .forms import PphFinalPaymentForm, TaxRateForm
from .models import PphFinalAccrual, PphFinalPayment
from .selectors import pph_final_accruals, pph_final_payments, pph_final_schedule, tax_rates_for


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
        today = timezone.localdate()
        annual_turnover = sum(m.gross_turnover for m in schedule)
        return super().get_context_data(
            year=year,
            years=year_choices(),
            rows=[
                PphFinalRow(m, accruals.get(m.month), payments.get(m.month), services.month_end(year, m.month) <= today)
                for m in schedule
            ],
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
