from django.urls import path

from . import views

app_name = "assets"

urlpatterns = [
    path("", views.AssetListView.as_view(), name="list"),
    path("new/", views.AssetCreateView.as_view(), name="create"),
    path("depreciation/", views.PostDepreciationView.as_view(), name="post_depreciation"),
    path("<int:pk>/", views.AssetDetailView.as_view(), name="detail"),
    path("<int:pk>/edit/", views.AssetUpdateView.as_view(), name="update"),
    path("<int:pk>/delete/", views.AssetDeleteView.as_view(), name="delete"),
    path("<int:pk>/dispose/", views.AssetDisposeView.as_view(), name="dispose"),
]
