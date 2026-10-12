from django.urls import path

from . import views

app_name = "reports"

urlpatterns = [
    path("", views.ReportIndexView.as_view(), name="index"),
    path("neraca-saldo/", views.TrialBalanceView.as_view(), name="trial_balance"),
    path("laba-rugi/", views.IncomeStatementView.as_view(), name="income_statement"),
    path("posisi-keuangan/", views.BalanceSheetView.as_view(), name="balance_sheet"),
    path("buku-besar/", views.GeneralLedgerView.as_view(), name="general_ledger"),
    path("umur/<invoice_kind:kind>/", views.AgingView.as_view(), name="aging"),
    path("ppn/", views.VatReportView.as_view(), name="vat"),
]
