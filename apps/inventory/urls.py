from django.urls import path

from . import views

app_name = "inventory"

urlpatterns = [
    path("", views.StockListView.as_view(), name="list"),
    path("adjust/", views.AdjustmentView.as_view(), name="adjust"),
    path("<int:pk>/", views.StockCardView.as_view(), name="card"),
]
