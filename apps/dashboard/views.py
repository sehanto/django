from django.utils import timezone
from django.views.generic import TemplateView

from apps.core.money import ZERO
from apps.invoicing.models import InvoiceKind
from apps.invoicing.selectors import invoices_for, outstanding_summary
from apps.tenants.mixins import OrganizationMixin

from .selectors import cash_position, monthly_results


class DashboardView(OrganizationMixin, TemplateView):
    template_name = "dashboard/home.html"

    def get_context_data(self, **kwargs):
        today = timezone.localdate()
        months = monthly_results(self.organization, until=today, months=6)
        cash = cash_position(self.organization, as_of=today)
        peak = max([max(m.revenue, m.expense) for m in months] + [ZERO])
        return super().get_context_data(
            today=today,
            cash=cash,
            cash_total=sum((balance for _, balance in cash), ZERO),
            receivables=outstanding_summary(self.organization, InvoiceKind.SALES),
            payables=outstanding_summary(self.organization, InvoiceKind.PURCHASE),
            months=months,
            this_month=months[-1],
            chart_peak=peak,
            recent_sales=invoices_for(self.organization, InvoiceKind.SALES)[:5],
            recent_purchases=invoices_for(self.organization, InvoiceKind.PURCHASE)[:5],
            **kwargs,
        )
