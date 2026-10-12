from urllib.parse import urlencode

from django.shortcuts import redirect
from django.urls import reverse

from .selectors import resolve_membership
from .session import get_current_organization_id, set_current_organization


class CurrentOrganizationMiddleware:
    """Attach ``request.organization`` and ``request.membership``.

    The active organization is stored in the session; if it is missing or the
    user no longer belongs to it, the user's first membership is used.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.organization = None
        request.membership = None
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            membership = resolve_membership(user, get_current_organization_id(request))
            if membership is not None:
                request.membership = membership
                request.organization = membership.organization
                set_current_organization(request, membership.organization)
        return self.get_response(request)


class TwoFactorRequirementMiddleware:
    """Enforce an organization's "require two-factor authentication" setting.

    The session must have *proved* a second factor (django-otp ``is_verified``), not
    just belong to a user who owns one. Members without 2FA go to the setup page;
    members whose session predates their 2FA are asked for a code. Setup, sign-out,
    the language switch and switching to another organization stay reachable.
    """

    EXEMPT = {
        ("accounts", name) for name in (
            "security", "two_factor_setup", "two_factor_backup_codes", "two_factor_verify_session",
            "logout", "set_language", "verify_email", "verify_email_pending", "verify_email_resend", "verify_email_change",
        )
    } | {("tenants", "switch"), ("tenants", "create")}

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        organization = getattr(request, "organization", None)
        if organization is None or not organization.require_two_factor or request.user.is_verified():
            return None
        match = request.resolver_match
        if match and (match.namespace == "admin" or (match.namespace, match.url_name) in self.EXEMPT):
            return None
        from apps.accounts import two_factor  # accounts depends on tenants; import at use

        if two_factor.is_enabled(request.user):
            return redirect(f"{reverse('accounts:two_factor_verify_session')}?{urlencode({'next': request.get_full_path()})}")
        return redirect("accounts:security")
