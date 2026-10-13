from django.urls import path

from . import views

app_name = "api"

urlpatterns = [
    path("accounts/", views.accounts, name="accounts"),
    path("contacts/", views.contacts, name="contacts"),
    path("items/", views.items, name="items"),
    path("tax-rates/", views.tax_rates, name="tax_rates"),
    path("invoices/", views.invoices, name="invoices"),
    path("invoices/<int:pk>/", views.invoice_detail, name="invoice"),
    path("invoices/<int:pk>/payments/", views.invoice_payments, name="invoice_payments"),
    path("invoices/<int:pk>/returns/", views.invoice_returns, name="invoice_returns"),
    path("journal-entries/", views.journal_entries, name="journal_entries"),
    path("reports/trial-balance/", views.trial_balance, name="trial_balance"),
]
