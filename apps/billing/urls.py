from django.urls import path

from . import views

app_name = "billing"

urlpatterns = [
    path("", views.OverviewView.as_view(), name="overview"),
    path("checkout/", views.CheckoutView.as_view(), name="checkout"),
    path("invoices/<int:pk>/", views.InvoiceDetailView.as_view(), name="invoice"),
    path("invoices/<int:pk>/refresh/", views.InvoiceRefreshView.as_view(), name="invoice_refresh"),
    path("webhooks/<slug:gateway>/", views.WebhookView.as_view(), name="webhook"),
    path("fake-checkout/<str:reference>/", views.FakeCheckoutView.as_view(), name="fake_checkout"),
    path("auto-renewal/", views.AutoRenewalStartView.as_view(), name="auto_renewal_start"),
    path("auto-renewal/return/", views.AutoRenewalReturnView.as_view(), name="auto_renewal_return"),
    path("auto-renewal/cancel/", views.AutoRenewalCancelView.as_view(), name="auto_renewal_cancel"),
    path("fake-save/<str:reference>/", views.FakeSaveView.as_view(), name="fake_save"),
]
