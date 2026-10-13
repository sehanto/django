from django.urls import path

from . import pages

app_name = "api_keys"

urlpatterns = [
    path("", pages.ApiKeysView.as_view(), name="list"),
    path("<int:pk>/revoke/", pages.RevokeApiKeyView.as_view(), name="revoke"),
]
