from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("akun/", include("apps.accounts.urls")),
    path("organisasi/", include("apps.tenants.urls")),
    path("buku-besar/", include("apps.ledger.urls")),
    path("kontak/", include("apps.contacts.urls")),
    path("pajak/", include("apps.taxes.urls")),
    path("transaksi/", include("apps.invoicing.urls")),
    path("laporan/", include("apps.reports.urls")),
    path("", include("apps.dashboard.urls")),
]
