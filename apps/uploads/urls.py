from django.urls import path

from . import views

app_name = "uploads"

urlpatterns = [
    path("", views.UploadIndexView.as_view(), name="index"),
    path("<slug:slug>/", views.UploadView.as_view(), name="upload"),
    path("<slug:slug>/apply/", views.UploadApplyView.as_view(), name="apply"),
    path("<slug:slug>/template.csv", views.UploadTemplateView.as_view(), name="template"),
]
