from django.contrib import admin
from django.urls import include, path
from django_otp.admin import OTPAdminSite

# The admin has its own login; require a verified second factor there too.
admin.site.__class__ = OTPAdminSite

urlpatterns = [
    path("admin/", admin.site.urls),
    path("account/", include("apps.accounts.urls")),
    path("organization/", include("apps.tenants.urls")),
    path("ledger/", include("apps.ledger.urls")),
    path("contacts/", include("apps.contacts.urls")),
    path("taxes/", include("apps.taxes.urls")),
    path("reports/", include("apps.reports.urls")),
    path("billing/", include("apps.billing.urls")),
    # Invoicing lives at the root: /sales/, /purchases/, /payments/, /items/.
    path("", include("apps.invoicing.urls")),
    path("", include("apps.dashboard.urls")),
]
