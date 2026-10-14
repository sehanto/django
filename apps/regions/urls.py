from django.urls import path

from . import views

app_name = "regions"

urlpatterns = [
    path("choices/", views.RegionChoicesView.as_view(), name="choices"),
]
