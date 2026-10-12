from django.urls import path

from . import views

app_name = "audit"

urlpatterns = [
    path("", views.OrganizationActivityView.as_view(), name="organization"),
]
