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
]
