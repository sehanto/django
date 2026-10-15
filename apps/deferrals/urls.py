from django.urls import path

from . import views

app_name = "deferrals"

urlpatterns = [
    path("", views.DeferralListView.as_view(), name="list"),
    path("new/<str:kind>/", views.DeferralCreateView.as_view(), name="create"),
    path("recognise/", views.PostDeferralsView.as_view(), name="post"),
    path("<int:pk>/", views.DeferralDetailView.as_view(), name="detail"),
    path("<int:pk>/void/", views.DeferralVoidView.as_view(), name="void"),
]
