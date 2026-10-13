from django.urls import path

from . import views

app_name = "invoicing"

urlpatterns = [
    path("payments/", views.PaymentListView.as_view(), name="payments"),
    path("payments/<int:pk>/void/", views.PaymentVoidView.as_view(), name="payment_void"),
    path("returns/<int:pk>/", views.ReturnDetailView.as_view(), name="return_detail"),
    path("returns/<int:pk>/void/", views.ReturnVoidView.as_view(), name="return_void"),
    path("items/", views.ItemListView.as_view(), name="items"),
    path("items/new/", views.ItemCreateView.as_view(), name="item_create"),
    path("items/<int:pk>/edit/", views.ItemUpdateView.as_view(), name="item_update"),
    path("<invoice_kind:kind>/", views.InvoiceListView.as_view(), name="list"),
    path("<invoice_kind:kind>/new/", views.InvoiceCreateView.as_view(), name="create"),
    path("<invoice_kind:kind>/<int:pk>/", views.InvoiceDetailView.as_view(), name="detail"),
    path("<invoice_kind:kind>/<int:pk>/print/", views.InvoicePrintView.as_view(), name="print"),
    path("<invoice_kind:kind>/<int:pk>/edit/", views.InvoiceUpdateView.as_view(), name="update"),
    path("<invoice_kind:kind>/<int:pk>/issue/", views.InvoiceIssueView.as_view(), name="issue"),
    path("<invoice_kind:kind>/<int:pk>/void/", views.InvoiceVoidView.as_view(), name="void"),
    path("<invoice_kind:kind>/<int:pk>/delete/", views.InvoiceDeleteView.as_view(), name="delete"),
    path("<invoice_kind:kind>/<int:pk>/pay/", views.PaymentCreateView.as_view(), name="pay"),
    path("<invoice_kind:kind>/<int:pk>/return/", views.ReturnCreateView.as_view(), name="return_create"),
]
