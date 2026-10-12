from django.urls import path

from . import views

app_name = "tenants"

urlpatterns = [
    path("pengaturan/", views.OrganizationSettingsView.as_view(), name="settings"),
    path("baru/", views.OrganizationCreateView.as_view(), name="create"),
    path("<int:pk>/pilih/", views.SwitchOrganizationView.as_view(), name="switch"),
    path("anggota/", views.MembersView.as_view(), name="members"),
    path("anggota/<int:pk>/hapus/", views.RemoveMemberView.as_view(), name="remove_member"),
    path("paket/", views.PlanView.as_view(), name="plans"),
]
