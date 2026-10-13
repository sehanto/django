from django.urls import path

from . import views

app_name = "tenants"

urlpatterns = [
    path("settings/", views.OrganizationSettingsView.as_view(), name="settings"),
    path("places/new/", views.BusinessPlaceCreateView.as_view(), name="place_create"),
    path("places/<int:pk>/edit/", views.BusinessPlaceUpdateView.as_view(), name="place_update"),
    path("new/", views.OrganizationCreateView.as_view(), name="create"),
    path("<int:pk>/switch/", views.SwitchOrganizationView.as_view(), name="switch"),
    path("members/", views.MembersView.as_view(), name="members"),
    path("members/<int:pk>/remove/", views.RemoveMemberView.as_view(), name="remove_member"),
    path("invitations/<int:pk>/revoke/", views.RevokeInvitationView.as_view(), name="revoke_invitation"),
    path("invitations/<str:token>/", views.InvitationView.as_view(), name="invitation"),
]
