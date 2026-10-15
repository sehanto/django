from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.utils.translation import gettext as _

from .roles import Permission


class OrganizationMixin(LoginRequiredMixin):
    """Require an active organization and the role-based ``permission``.

    Exposes ``self.organization`` and ``self.membership`` to subclasses so
    that every query can be scoped explicitly to the current tenant.
    """

    permission = Permission.VIEW

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if request.organization is None:
            return redirect("tenants:create")
        if not request.membership.has_permission(self.permission):
            raise PermissionDenied(_("Your role does not have access to this page."))
        self.organization = request.organization
        self.membership = request.membership
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["organization"] = self.organization
        return kwargs


class WriteMixin(OrganizationMixin):
    """Pages that record something: not for an organization that is read-only until it pays
    (its writes are refused anyway, ReadOnlyOrganizationMiddleware; this also spares the form)."""

    permission = Permission.WRITE

    def dispatch(self, request, *args, **kwargs):
        organization = getattr(request, "organization", None)
        if request.user.is_authenticated and organization is not None and organization.is_read_only:
            messages.error(request, _("This organization is read-only until its plan is paid."))
            return redirect("billing:overview")
        return super().dispatch(request, *args, **kwargs)


class ManageMixin(OrganizationMixin):
    permission = Permission.MANAGE
