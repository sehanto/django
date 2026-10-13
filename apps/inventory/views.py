from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.generic import FormView, TemplateView

from apps.core.exceptions import DomainError
from apps.core.money import ZERO
from apps.core.params import int_param
from apps.tenants.mixins import OrganizationMixin, WriteMixin

from . import services
from .forms import AdjustmentForm, WriteDownForm
from .selectors import reconcile, stock_card, stock_summary, tracked_items


class StockListView(OrganizationMixin, TemplateView):
    template_name = "inventory/stock_list.html"

    def get_context_data(self, **kwargs):
        rows = stock_summary(self.organization)
        return super().get_context_data(
            rows=rows,
            total_value=sum((r.value for r in rows), ZERO),
            reconciliation=reconcile(self.organization, rows, timezone.localdate()),
            **kwargs,
        )


class StockCardView(OrganizationMixin, TemplateView):
    template_name = "inventory/stock_card.html"

    def get_context_data(self, **kwargs):
        item = get_object_or_404(tracked_items(self.organization), pk=self.kwargs["pk"])
        rows = stock_card(item)
        return super().get_context_data(item=item, rows=rows, last=rows[-1] if rows else None, **kwargs)


class AdjustmentView(WriteMixin, FormView):
    template_name = "inventory/adjustment_form.html"
    form_class = AdjustmentForm

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "organization": self.organization}

    def get_initial(self):
        return {"on": timezone.localdate(), "item": int_param(self.request.GET, "item", minimum=1)}

    def form_valid(self, form):
        data = form.cleaned_data
        try:
            movement = services.adjust_stock(organization=self.organization, created_by=self.request.user, **data)
        except DomainError as exc:
            form.add_error(None, str(exc))
            return self.form_invalid(form)
        messages.success(self.request, _("Stock adjusted and posted to the ledger."))
        return redirect("inventory:card", movement.item_id)


class WriteDownView(WriteMixin, FormView):
    template_name = "inventory/write_down_form.html"
    form_class = WriteDownForm

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "organization": self.organization}

    def get_initial(self):
        return {"on": timezone.localdate(), "item": int_param(self.request.GET, "item", minimum=1)}

    def form_valid(self, form):
        try:
            movement = services.write_down_stock(organization=self.organization, created_by=self.request.user, **form.cleaned_data)
        except DomainError as exc:
            form.add_error(None, str(exc))
            return self.form_invalid(form)
        messages.success(self.request, _("Stock written down to its net realisable value and posted to the ledger."))
        return redirect("inventory:card", movement.item_id)
