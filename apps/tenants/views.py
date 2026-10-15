from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils.translation import gettext as _
from django.utils.translation import ngettext
from django.views import View
from django.views.generic import CreateView, FormView, UpdateView

from apps.audit import services as audit
from apps.audit.domain import AuditAction
from apps.core.exceptions import DomainError
from apps.core.views import DomainErrorFormMixin, flash_domain_error
from apps.ledger import services as ledger

from . import services
from .forms import AddMemberForm, BusinessPlaceForm, OrganizationCreateForm, OrganizationSettingsForm, reopens_period
from .models import BusinessPlace, Invitation
from .mixins import ManageMixin
from .selectors import business_places, members_of, organizations_for_user, pending_invitations
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
        return super().get_context_data(places=business_places(self.organization), **kwargs)

    def form_valid(self, form):
        with transaction.atomic():
            response = super().form_valid(form)
            if "legal_form" in form.changed_data:
                self._adopt_equity(form.initial["legal_form"])
        if form.changed_data:
            audit.record(
                AuditAction.ORGANIZATION_SETTINGS_CHANGED, request=self.request, organization=self.object,
                changes={name: [form.initial.get(name), form.cleaned_data[name]] for name in form.changed_data},
            )
        if reopens_period(form.initial["lock_date"], form.cleaned_data["lock_date"]):
            audit.record(
                AuditAction.PERIOD_REOPENED, request=self.request, organization=self.object,
                locked_until=form.initial["lock_date"], lock_date=form.cleaned_data["lock_date"],
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

    def _adopt_equity(self, previous_legal_form):
        """The chart's equity accounts follow the new legal form (SAK EMKM Bab 13)."""
        change = ledger.adopt_legal_form_equity(organization=self.object, previous_legal_form=previous_legal_form)
        if change.adopted:
            messages.info(self.request, _(
                "The chart of accounts now has the equity accounts of the new legal form: %(accounts)s. Accounts with "
                "transactions keep their name."
            ) % {"accounts": ", ".join(change.adopted)})
        if change.deactivated:
            messages.info(self.request, _("Deactivated, as the new legal form doesn't use them: %(accounts)s.") % {
                "accounts": ", ".join(change.deactivated)})


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
        return super().get_context_data(
            members=members_of(self.organization), invitations=pending_invitations(self.organization), **kwargs
        )

    def perform(self, form):
        invitation = services.invite_member(organization=self.organization, invited_by=self.request.user, **form.cleaned_data)
        audit.record(AuditAction.MEMBER_INVITED, request=self.request, organization=self.organization, target=invitation.email,
                     role=invitation.role)
        messages.success(self.request, _("Invitation sent to %(email)s.") % {"email": invitation.email})
        return redirect(self.success_url)


class RevokeInvitationView(ManageMixin, View):
    def post(self, request, pk):
        invitation = get_object_or_404(Invitation, organization=self.organization, pk=pk)
        services.revoke_invitation(organization=self.organization, invitation_id=invitation.pk)
        audit.record(AuditAction.INVITATION_REVOKED, request=request, organization=self.organization, target=invitation.email)
        messages.success(request, _("Invitation revoked."))
        return redirect("tenants:members")


class InvitationView(LoginRequiredMixin, View):
    """The link in an invitation email: shows the invitation and accepts it."""

    template_name = "tenants/invitation.html"

    def get(self, request, token):
        invitation = services.find_invitation(token)
        return render(request, self.template_name, {"invitation": invitation if invitation and invitation.is_pending else None})

    def post(self, request, token):
        try:
            membership = services.accept_invitation(token=token, user=request.user)
        except DomainError as exc:
            flash_domain_error(request, exc)
            return redirect("tenants:invitation", token=token)
        audit.record(
            AuditAction.MEMBER_ADDED, request=request, organization=membership.organization,
            target=request.user.email, role=membership.role, via="invitation",
        )
        set_current_organization(request, membership.organization)
        messages.success(request, _("Welcome to %(organization)s.") % {"organization": membership.organization.name})
        return redirect("dashboard:home")


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
