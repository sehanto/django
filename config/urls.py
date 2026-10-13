from django.contrib import admin
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.urls import include, path, reverse
from django.utils.http import urlencode
from django_otp.admin import OTPAdminSite


class AdminSite(OTPAdminSite):
    """The admin requires a verified second factor and has no login form of its own:
    everyone signs in on the site's page, where rate limiting, the CAPTCHA and 2FA apply."""

    def login(self, request, extra_context=None):
        next_url = request.GET.get("next") or reverse("admin:index")
        user = request.user
        if not user.is_authenticated:
            return redirect_to_login(next_url, login_url=reverse("accounts:login"))
        if user.is_active and user.is_staff and not user.is_verified():  # password-only session: prove 2FA
            return redirect(f"{reverse('accounts:two_factor_verify_session')}?{urlencode({'next': next_url})}")
        raise PermissionDenied  # signed in, but not staff


admin.site.__class__ = AdminSite

urlpatterns = [
    path("admin/", admin.site.urls),
    path("account/", include("apps.accounts.urls")),
    path("organization/activity/", include("apps.audit.urls")),
    path("organization/api-keys/", include("apps.api.page_urls")),
    path("organization/", include("apps.tenants.urls")),
    path("ledger/", include("apps.ledger.urls")),
    path("contacts/", include("apps.contacts.urls")),
    path("taxes/", include("apps.taxes.urls")),
    path("reports/", include("apps.reports.urls")),
    path("fixed-assets/", include("apps.assets.urls")),
    path("inventory/", include("apps.inventory.urls")),
    path("payroll/", include("apps.payroll.urls")),
    path("billing/", include("apps.billing.urls")),
    path("api/v1/", include("apps.api.urls")),
    # Invoicing lives at the root: /sales/, /purchases/, /payments/, /items/.
    path("", include("apps.invoicing.urls")),
    path("", include("apps.dashboard.urls")),
]
