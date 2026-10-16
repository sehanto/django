from urllib.parse import urlencode

from django.contrib import messages
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _

from .domain import PlanFeature
from .selectors import first_plan_with, resolve_membership
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
            "logout", "set_language", "set_theme", "verify_email", "verify_email_pending", "verify_email_resend", "verify_email_change",
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


SAFE_METHODS = ("GET", "HEAD", "OPTIONS")


class ReadOnlyOrganizationMiddleware:
    """An organization whose trial, or paid period and grace days, has ended records nothing new
    until it pays: every write is refused, whatever the page. Paying (billing), the settings and
    team, the account itself, signing out and exporting the books stay open; so does reading
    everything. The API refuses writes the same way (402, apps/api/views.py)."""

    OPEN = {"accounts", "billing", "tenants", "admin", "api"}  # the API answers in JSON itself
    OPEN_VIEWS = {("reports", "export_books")}

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        organization = getattr(request, "organization", None)
        if request.method in SAFE_METHODS or organization is None or not organization.is_read_only:
            return None
        match = request.resolver_match
        if match and (match.namespace in self.OPEN or (match.namespace, match.url_name) in self.OPEN_VIEWS):
            return None
        messages.error(request, _("This organization is read-only until its plan is paid. Nothing was recorded."))
        back = request.META.get("HTTP_REFERER", "")
        if back and url_has_allowed_host_and_scheme(back, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
            return redirect(back)
        return redirect("billing:overview")


class PlanFeatureMiddleware:
    """What a plan doesn't include answers with the page that names the plan that does. Only the
    feature's own pages are gated: what was recorded with it stays in the books, the reports and
    the export, and the nightly jobs keep posting it (e.g. depreciation of registered assets).
    Files for Coretax and eBupot stay downloadable on every plan; sending them is the feature."""

    FEATURE_SECTIONS = {"payroll": PlanFeature.PAYROLL, "assets": PlanFeature.FIXED_ASSETS, "api_keys": PlanFeature.API}
    FEATURE_VIEWS = {
        ("invoicing", name): PlanFeature.CORETAX_SENDING
        for name in ("coretax_connection", "coretax_send_month", "coretax_send_returns", "coretax_send_invoice", "coretax_refresh")
    } | {("taxes", "ebupot_send"): PlanFeature.CORETAX_SENDING}

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        organization, match = getattr(request, "organization", None), request.resolver_match
        if organization is None or match is None:
            return None
        feature = self.FEATURE_VIEWS.get((match.namespace, match.url_name)) or self.FEATURE_SECTIONS.get(match.namespace)
        if feature is None or organization.has_feature(feature):
            return None
        return render(request, "tenants/upgrade.html", {"feature": feature, "plan": first_plan_with(feature)}, status=403)
