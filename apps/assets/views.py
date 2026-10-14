from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import ngettext
from django.views import View
from django.views.generic import CreateView, DetailView, TemplateView, UpdateView

from apps.core.exceptions import DomainError
from apps.core.money import ZERO
from apps.core.views import flash_domain_error
from apps.tenants.mixins import OrganizationMixin, WriteMixin

from . import services
from .forms import DisposalForm, FixedAssetForm, RevisionForm
from .selectors import accumulated_depreciation, assets_for, due_depreciation, posted_amounts, reconcile, register


class AssetListView(OrganizationMixin, TemplateView):
    template_name = "assets/asset_list.html"

    def get_context_data(self, **kwargs):
        today = timezone.localdate()
        rows = register(self.organization)
        due = due_depreciation(self.organization, today)
        lock_date = self.organization.lock_date
        active = [r for r in rows if r.asset.status == r.asset.Status.ACTIVE]
        return super().get_context_data(
            rows=rows,
            total_cost=sum((r.asset.cost for r in active), ZERO),
            total_accumulated=sum((r.accumulated for r in active), ZERO),
            total_book_value=sum((r.book_value for r in active), ZERO),
            due_months=len(due),
            due_amount=sum((m.amount for items in due.values() for _a, m in items), ZERO),
            locked_due=bool(lock_date) and any(m.period_end <= lock_date for items in due.values() for _a, m in items),
            reconciliation=reconcile(self.organization, rows, today),
            **kwargs,
        )


class PostDepreciationView(WriteMixin, View):
    def post(self, request):
        try:
            entries = services.post_depreciation(organization=self.organization, through=timezone.localdate(), created_by=request.user)
        except DomainError as exc:
            flash_domain_error(request, exc)
        else:
            if entries:
                messages.success(request, ngettext(
                    "Depreciation posted for %(count)d month.", "Depreciation posted for %(count)d months.", len(entries)
                ) % {"count": len(entries)})
            else:
                messages.info(request, _("No depreciation is due."))
        return redirect("assets:list")


class AssetFormMixin(WriteMixin):
    form_class = FixedAssetForm
    template_name = "assets/asset_form.html"

    def get_queryset(self):
        return assets_for(self.organization)

    def form_valid(self, form):
        try:
            asset = self.save(form.cleaned_data)
        except DomainError as exc:
            form.add_error(None, str(exc))
            return self.form_invalid(form)
        messages.success(self.request, _("Fixed asset saved."))
        return redirect("assets:detail", asset.pk)


class AssetCreateView(AssetFormMixin, CreateView):
    def save(self, data):
        return services.register_asset(organization=self.organization, created_by=self.request.user, **data)


class AssetUpdateView(AssetFormMixin, UpdateView):
    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "locked": services.is_locked(self.object)}

    def save(self, data):
        return services.update_asset(asset=self.object, **data)


class AssetDeleteView(WriteMixin, View):
    def post(self, request, pk):
        asset = get_object_or_404(assets_for(self.organization), pk=pk)
        try:
            services.delete_asset(asset=asset)
        except DomainError as exc:
            flash_domain_error(request, exc)
            return redirect("assets:detail", asset.pk)
        messages.success(request, _("Fixed asset deleted."))
        return redirect("assets:list")


class AssetDetailView(OrganizationMixin, DetailView):
    template_name = "assets/asset_detail.html"
    context_object_name = "asset"

    def get_queryset(self):
        return assets_for(self.organization)

    def get_context_data(self, **kwargs):
        asset = self.object
        posted = posted_amounts(asset)
        today = timezone.localdate()
        schedule = [(m, posted.get(m.key), asset.is_booked_elsewhere(m)) for m in asset.schedule]
        accumulated = accumulated_depreciation(asset)
        locked = services.is_locked(asset)
        can_revise = locked and asset.status == asset.Status.ACTIVE and asset.is_depreciable
        return super().get_context_data(
            schedule=schedule,
            accumulated=accumulated,
            book_value=ZERO if asset.status == asset.Status.DISPOSED else asset.cost - accumulated,
            today=today,
            locked=locked,
            disposal_form=DisposalForm(organization=self.organization, initial={"on": today}),
            revisions=asset.estimates,
            revision_form=RevisionForm(initial={
                "effective_from": services.first_revisable_month(asset), "useful_life_months": asset.current_useful_life_months,
                "method": asset.current_method,
            }) if can_revise else None,
            **kwargs,
        )


class AssetDisposeView(WriteMixin, View):
    def post(self, request, pk):
        asset = get_object_or_404(assets_for(self.organization), pk=pk)
        form = DisposalForm(request.POST, organization=self.organization)
        if not form.is_valid():
            messages.error(request, _("The disposal details are incomplete."))
            return redirect("assets:detail", asset.pk)
        try:
            services.dispose_asset(asset=asset, created_by=request.user, **form.service_kwargs())
            messages.success(request, _("Fixed asset disposed and posted to the ledger."))
        except DomainError as exc:
            flash_domain_error(request, exc)
        return redirect(reverse("assets:detail", args=[asset.pk]))


class AssetReviseView(WriteMixin, View):
    def post(self, request, pk):
        asset = get_object_or_404(assets_for(self.organization), pk=pk)
        form = RevisionForm(request.POST)
        if not form.is_valid():
            messages.error(request, _("The new estimate is incomplete."))
            return redirect("assets:detail", asset.pk)
        try:
            services.revise_estimate(asset=asset, created_by=request.user, **form.cleaned_data)
            messages.success(request, _("Estimate revised. Depreciation from that month on follows the new schedule."))
        except DomainError as exc:
            flash_domain_error(request, exc)
        return redirect("assets:detail", asset.pk)
