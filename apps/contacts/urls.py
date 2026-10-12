from django.urls import path

from . import views

app_name = "contacts"

urlpatterns = [
    path("", views.ContactListView.as_view(), name="list"),
    path("baru/", views.ContactCreateView.as_view(), name="create"),
    path("<int:pk>/ubah/", views.ContactUpdateView.as_view(), name="update"),
    path("<int:pk>/hapus/", views.ContactDeleteView.as_view(), name="delete"),
]
