from django.urls import path

from . import views

app_name = "tenants"

urlpatterns = [
    path("settings/", views.OrganizationSettingsView.as_view(), name="settings"),
    path("new/", views.OrganizationCreateView.as_view(), name="create"),
    path("<int:pk>/switch/", views.SwitchOrganizationView.as_view(), name="switch"),
    path("members/", views.MembersView.as_view(), name="members"),
    path("members/<int:pk>/remove/", views.RemoveMemberView.as_view(), name="remove_member"),
    path("plan/", views.PlanView.as_view(), name="plans"),
]
