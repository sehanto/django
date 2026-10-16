from django.urls import path

from . import views

app_name = "banking"

urlpatterns = [
    path("", views.BankAccountListView.as_view(), name="accounts"),
    path("<int:pk>/", views.ReconciliationView.as_view(), name="reconciliation"),
    path("lines/<int:pk>/<str:action>/", views.StatementLineActionView.as_view(), name="line_action"),
]
