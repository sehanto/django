from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import FormView, TemplateView, UpdateView

from apps.core.exceptions import DomainError
from apps.core.views import DomainErrorFormMixin, flash_domain_error

from . import services
from .forms import AddMemberForm, OrganizationCreateForm, OrganizationSettingsForm
from .mixins import ManageMixin
from .models import Plan
from .selectors import available_plans, members_of, organizations_for_user
from .session import set_current_organization


class OrganizationSettingsView(ManageMixin, UpdateView):
    template_name = "tenants/settings.html"
    form_class = OrganizationSettingsForm
    success_url = reverse_lazy("tenants:settings")

    def get_object(self, queryset=None):
        return self.organization

    def form_valid(self, form):
        messages.success(self.request, "Pengaturan organisasi disimpan.")
        return super().form_valid(form)


class OrganizationCreateView(LoginRequiredMixin, FormView):
    template_name = "tenants/create.html"
    form_class = OrganizationCreateForm

    def form_valid(self, form):
        organization = services.create_organization(owner=self.request.user, **form.cleaned_data)
        set_current_organization(self.request, organization)
        messages.success(self.request, f"Organisasi {organization.name} berhasil dibuat.")
        return redirect("tenants:settings")


class SwitchOrganizationView(LoginRequiredMixin, View):
    def post(self, request, pk):
        organization = get_object_or_404(organizations_for_user(request.user), pk=pk)
        set_current_organization(request, organization)
        return redirect("dashboard:home")


class MembersView(ManageMixin, DomainErrorFormMixin, FormView):
    template_name = "tenants/members.html"
    form_class = AddMemberForm
    success_url = reverse_lazy("tenants:members")

    def get_context_data(self, **kwargs):
        return super().get_context_data(members=members_of(self.organization), **kwargs)

    def perform(self, form):
        services.add_member(organization=self.organization, **form.cleaned_data)
        messages.success(self.request, "Anggota berhasil ditambahkan.")
        return redirect(self.success_url)


class RemoveMemberView(ManageMixin, View):
    def post(self, request, pk):
        try:
            services.remove_member(organization=self.organization, membership_id=pk, acting_user=request.user)
            messages.success(request, "Anggota dihapus.")
        except DomainError as exc:
            flash_domain_error(request, exc)
        return redirect("tenants:members")


class PlanView(ManageMixin, TemplateView):
    template_name = "tenants/plans.html"

    def get_context_data(self, **kwargs):
        return super().get_context_data(plans=available_plans(), **kwargs)

    def post(self, request):
        plan = get_object_or_404(Plan, pk=request.POST.get("plan"))
        try:
            services.change_plan(organization=self.organization, plan=plan)
            messages.success(request, f"Paket berhasil diubah ke {plan.name}.")
        except DomainError as exc:
            flash_domain_error(request, exc)
        return redirect("tenants:plans")
