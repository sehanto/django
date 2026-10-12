from django.urls import path

from . import views

app_name = "taxes"

urlpatterns = [
    path("tarif/", views.TaxRateListView.as_view(), name="rates"),
    path("tarif/baru/", views.TaxRateCreateView.as_view(), name="rate_create"),
    path("tarif/<int:pk>/ubah/", views.TaxRateUpdateView.as_view(), name="rate_update"),
    path("pph-final/", views.PphFinalView.as_view(), name="pph_final"),
    path("pph-final/setor/", views.PphFinalPayView.as_view(), name="pph_final_pay"),
    path("pph-final/<int:pk>/batal/", views.PphFinalCancelView.as_view(), name="pph_final_cancel"),
]
