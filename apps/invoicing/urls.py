from django.urls import path

from . import views

app_name = "invoicing"

urlpatterns = [
    path("pembayaran/", views.PaymentListView.as_view(), name="payments"),
    path("pembayaran/<int:pk>/batal/", views.PaymentVoidView.as_view(), name="payment_void"),
    path("barang-jasa/", views.ItemListView.as_view(), name="items"),
    path("barang-jasa/baru/", views.ItemCreateView.as_view(), name="item_create"),
    path("barang-jasa/<int:pk>/ubah/", views.ItemUpdateView.as_view(), name="item_update"),
    path("<invoice_kind:kind>/", views.InvoiceListView.as_view(), name="list"),
    path("<invoice_kind:kind>/baru/", views.InvoiceCreateView.as_view(), name="create"),
    path("<invoice_kind:kind>/<int:pk>/", views.InvoiceDetailView.as_view(), name="detail"),
    path("<invoice_kind:kind>/<int:pk>/cetak/", views.InvoicePrintView.as_view(), name="print"),
    path("<invoice_kind:kind>/<int:pk>/ubah/", views.InvoiceUpdateView.as_view(), name="update"),
    path("<invoice_kind:kind>/<int:pk>/terbitkan/", views.InvoiceIssueView.as_view(), name="issue"),
    path("<invoice_kind:kind>/<int:pk>/batal/", views.InvoiceVoidView.as_view(), name="void"),
    path("<invoice_kind:kind>/<int:pk>/hapus/", views.InvoiceDeleteView.as_view(), name="delete"),
    path("<invoice_kind:kind>/<int:pk>/bayar/", views.PaymentCreateView.as_view(), name="pay"),
]
