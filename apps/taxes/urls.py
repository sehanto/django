from django.urls import path

from . import views

app_name = "taxes"

urlpatterns = [
    path("rates/", views.TaxRateListView.as_view(), name="rates"),
    path("rates/new/", views.TaxRateCreateView.as_view(), name="rate_create"),
    path("rates/<int:pk>/edit/", views.TaxRateUpdateView.as_view(), name="rate_update"),
    path("pph-final/", views.PphFinalView.as_view(), name="pph_final"),
    path("pph-final/accrue/", views.PphFinalAccrueView.as_view(), name="pph_final_accrue"),
    path("pph-final/expenses/<int:pk>/cancel/", views.PphFinalAccrualCancelView.as_view(), name="pph_final_accrual_cancel"),
    path("pph-final/pay/", views.PphFinalPayView.as_view(), name="pph_final_pay"),
    path("pph-final/<int:pk>/cancel/", views.PphFinalCancelView.as_view(), name="pph_final_cancel"),
    path("withholding/", views.WithholdingView.as_view(), name="withholding"),
    path("withholding/deposit/", views.WithholdingDepositView.as_view(), name="withholding_deposit"),
    path("withholding/deposits/<int:pk>/cancel/", views.WithholdingDepositCancelView.as_view(), name="withholding_deposit_cancel"),
    path("withholding/<int:year>/<int:month>/", views.WithholdingMonthView.as_view(), name="withholding_month"),
    path("withholding/<int:year>/<int:month>.csv", views.WithholdingMonthView.as_view(), {"csv": True}, name="withholding_month_csv"),
    path("withholding/<int:year>/<int:month>/ebupot/<str:kind>.xml", views.EbupotXmlView.as_view(), name="ebupot_xml"),
    path("withholding/<int:year>/<int:month>/ebupot/<str:kind>/send/", views.EbupotSendView.as_view(), name="ebupot_send"),
    path("withholding/payments/<int:pk>/tax-object/", views.PaymentTaxObjectView.as_view(), name="payment_tax_object"),
]
