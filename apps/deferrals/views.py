from django.contrib import messages
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import ngettext
from django.views import View
from django.views.generic import DetailView, FormView, TemplateView

from apps.core.exceptions import DomainError
from apps.core.money import ZERO
from apps.core.views import flash_domain_error
from apps.tenants.mixins import OrganizationMixin, WriteMixin

from . import services
from .domain import DeferralKind
from .forms import DeferralForm
from .selectors import deferrals_for, due_recognition, postings_of, register


class DeferralListView(OrganizationMixin, TemplateView):
    template_name = "deferrals/deferral_list.html"

    def get_context_data(self, **kwargs):
        rows = register(self.organization)
        due = due_recognition(self.organization, timezone.localdate())
        lock_date = self.organization.lock_date
        return super().get_context_data(
            rows=rows, kinds=DeferralKind,
            due_months=len(due),
            locked_due=bool(lock_date) and any(m.period_end <= lock_date for items in due.values() for _deferral, m in items),
            **kwargs,
        )


class PostDeferralsView(WriteMixin, View):
    def post(self, request):
        try:
            entries = services.post_deferrals(organization=self.organization, through=timezone.localdate(), created_by=request.user)
        except DomainError as exc:
            flash_domain_error(request, exc)
        else:
            if entries:
                messages.success(request, ngettext(
                    "Prepayments recognised for %(count)d month.", "Prepayments recognised for %(count)d months.", len(entries)
                ) % {"count": len(entries)})
            else:
                messages.info(request, _("Nothing is due."))
        return redirect("deferrals:list")


class DeferralCreateView(WriteMixin, FormView):
    template_name = "deferrals/deferral_form.html"
    form_class = DeferralForm

    def dispatch(self, request, *args, **kwargs):
        if kwargs["kind"] not in DeferralKind.values:
            raise Http404
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "kind": self.kwargs["kind"]}

    def get_context_data(self, **kwargs):
        return super().get_context_data(kind=DeferralKind(self.kwargs["kind"]), **kwargs)

    def form_valid(self, form):
        try:
            deferral = services.register_deferral(organization=self.organization, created_by=self.request.user, **form.service_kwargs())
        except DomainError as exc:
            form.add_error(None, str(exc))
            return self.form_invalid(form)
        messages.success(self.request, _("Prepayment registered. Each month is recognised after it ends."))
        return redirect("deferrals:detail", deferral.pk)


class DeferralDetailView(OrganizationMixin, DetailView):
    template_name = "deferrals/deferral_detail.html"
    context_object_name = "deferral"

    def get_queryset(self):
        return deferrals_for(self.organization)

    def get_context_data(self, **kwargs):
        deferral = self.object
        postings = list(postings_of(deferral))
        entries = {(p.year, p.month): p.journal_entry for p in postings}
        recognised = sum((p.amount for p in postings), ZERO)
        return super().get_context_data(
            schedule=[(m, entries.get(m.key)) for m in deferral.schedule],
            recognised=recognised,
            remaining=deferral.amount - recognised if deferral.status == deferral.Status.ACTIVE else ZERO,
            can_void=deferral.status == deferral.Status.ACTIVE and not entries,
            **kwargs,
        )


class DeferralVoidView(WriteMixin, View):
    def post(self, request, pk):
        deferral = get_object_or_404(deferrals_for(self.organization), pk=pk)
        try:
            services.void_deferral(deferral=deferral, on=timezone.localdate(), voided_by=request.user)
            messages.success(request, _("Prepayment voided."))
        except DomainError as exc:
            flash_domain_error(request, exc)
        return redirect("deferrals:detail", deferral.pk)
