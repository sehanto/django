from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views import View
from django.views.generic import CreateView, ListView, TemplateView, UpdateView

from apps.core.exceptions import DomainError
from apps.core.params import year_choices, year_param
from apps.core.views import flash_domain_error
from apps.tenants.mixins import OrganizationMixin, WriteMixin

from . import services
from .domain import MSME_TURNOVER_LIMIT, PPH_FINAL_RATE
from .forms import PphFinalPaymentForm, TaxRateForm
from .models import PphFinalPayment
from .selectors import pph_final_payments, pph_final_schedule, tax_rates_for


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


class PphFinalView(OrganizationMixin, TemplateView):
    template_name = "taxes/pph_final.html"

    def get_context_data(self, **kwargs):
        year = _selected_year(self.request)
        schedule = pph_final_schedule(self.organization, year)
        payments = pph_final_payments(self.organization, year)
        annual_turnover = sum(m.gross_turnover for m in schedule)
        return super().get_context_data(
            year=year,
            years=year_choices(),
            rows=[(m, payments.get(m.month)) for m in schedule],
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
        payment = get_object_or_404(PphFinalPayment.objects.for_organization(self.organization), pk=pk)
        year = payment.year
        try:
            services.cancel_pph_final_payment(payment=payment, on=timezone.localdate(), created_by=request.user)
            messages.success(request, _("PPh Final payment cancelled (journal entry reversed)."))
        except DomainError as exc:
            flash_domain_error(request, exc)
        return redirect(f"{reverse('taxes:pph_final')}?year={year}")
