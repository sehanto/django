from django.urls import path

from . import views

app_name = "reports"

urlpatterns = [
    path("", views.ReportIndexView.as_view(), name="index"),
    path("trial-balance/", views.TrialBalanceView.as_view(), name="trial_balance"),
    path("income-statement/", views.IncomeStatementView.as_view(), name="income_statement"),
    path("balance-sheet/", views.BalanceSheetView.as_view(), name="balance_sheet"),
    path("general-ledger/", views.GeneralLedgerView.as_view(), name="general_ledger"),
    path("aging/<invoice_kind:kind>/", views.AgingView.as_view(), name="aging"),
    path("vat/", views.VatReportView.as_view(), name="vat"),
]
