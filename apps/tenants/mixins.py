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
    permission = Permission.WRITE


class ManageMixin(OrganizationMixin):
    permission = Permission.MANAGE
