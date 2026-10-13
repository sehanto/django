from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.utils.translation import gettext as _
from django.utils.translation import ngettext
from django.views import View
from django.views.generic import CreateView, FormView, UpdateView

from apps.audit import services as audit
from apps.audit.domain import AuditAction
from apps.core.exceptions import DomainError
from apps.core.views import DomainErrorFormMixin, flash_domain_error

from . import services
from .forms import AddMemberForm, BusinessPlaceForm, OrganizationCreateForm, OrganizationSettingsForm
from .models import BusinessPlace
from .mixins import ManageMixin
from .selectors import members_of, organizations_for_user
from .session import set_current_organization


class OrganizationSettingsView(ManageMixin, UpdateView):
    template_name = "tenants/settings.html"
    form_class = OrganizationSettingsForm
    success_url = reverse_lazy("tenants:settings")

    def get_object(self, queryset=None):
        return self.organization

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "acting_user": self.request.user}

    def get_context_data(self, **kwargs):
        return super().get_context_data(places=BusinessPlace.objects.for_organization(self.organization), **kwargs)

    def form_valid(self, form):
        response = super().form_valid(form)
        if form.changed_data:
            audit.record(
                AuditAction.ORGANIZATION_SETTINGS_CHANGED, request=self.request, organization=self.object,
                changes={name: [form.initial.get(name), form.cleaned_data[name]] for name in form.changed_data},
            )
        # ModelForm already applied the new value to the instance, so use changed_data.
        if "require_two_factor" in form.changed_data and form.cleaned_data["require_two_factor"]:
            notified = services.notify_two_factor_requirement(organization=self.object, enabled_by=self.request.user)
            messages.info(self.request, ngettext(
                "Two-factor authentication is now required. %(count)s member without it was notified by email.",
                "Two-factor authentication is now required. %(count)s members without it were notified by email.",
                notified,
            ) % {"count": notified})
        messages.success(self.request, _("Organization settings saved."))
        return response


class BusinessPlaceFormMixin(ManageMixin):
    form_class = BusinessPlaceForm
    template_name = "tenants/place_form.html"
    success_url = reverse_lazy("tenants:settings")

    def get_queryset(self):
        return BusinessPlace.objects.for_organization(self.organization)

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "organization": self.organization}

    def form_valid(self, form):
        response = super().form_valid(form)
        audit.record(
            AuditAction.BUSINESS_PLACE_SAVED, request=self.request, organization=self.organization, target=self.object,
            nitku=self.object.nitku, active=self.object.is_active,
        )
        messages.success(self.request, _("Place of business saved."))
        return response


class BusinessPlaceCreateView(BusinessPlaceFormMixin, CreateView):
    pass


class BusinessPlaceUpdateView(BusinessPlaceFormMixin, UpdateView):
    pass


class OrganizationCreateView(LoginRequiredMixin, FormView):
    template_name = "tenants/create.html"
    form_class = OrganizationCreateForm

    def form_valid(self, form):
        organization = services.create_organization(owner=self.request.user, **form.cleaned_data)
        set_current_organization(self.request, organization)
        messages.success(self.request, _("Organization %(name)s created.") % {"name": organization.name})
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
        membership = services.add_member(organization=self.organization, **form.cleaned_data)
        audit.record(
            AuditAction.MEMBER_ADDED, request=self.request, organization=self.organization,
            target=membership.user.email, role=membership.role,
        )
        messages.success(self.request, _("Member added."))
        return redirect(self.success_url)


class RemoveMemberView(ManageMixin, View):
    def post(self, request, pk):
        try:
            removed = services.remove_member(organization=self.organization, membership_id=pk, acting_user=request.user)
            audit.record(
                AuditAction.MEMBER_REMOVED, request=request, organization=self.organization,
                target=removed.user.email, role=removed.role,
            )
            messages.success(request, _("Member removed."))
        except DomainError as exc:
            flash_domain_error(request, exc)
        return redirect("tenants:members")

