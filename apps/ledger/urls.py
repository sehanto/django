from django.urls import path

from . import views

app_name = "ledger"

urlpatterns = [
    path("accounts/", views.AccountListView.as_view(), name="accounts"),
    path("accounts/new/", views.AccountCreateView.as_view(), name="account_create"),
    path("accounts/<int:pk>/edit/", views.AccountUpdateView.as_view(), name="account_update"),
    path("accounts/<int:pk>/delete/", views.AccountDeleteView.as_view(), name="account_delete"),
    path("journal/", views.JournalListView.as_view(), name="journals"),
    path("journal/new/", views.JournalCreateView.as_view(), name="journal_create"),
    path("journal/<int:pk>/", views.JournalDetailView.as_view(), name="journal_detail"),
    path("journal/<int:pk>/edit/", views.JournalUpdateView.as_view(), name="journal_update"),
    path("journal/<int:pk>/post/", views.JournalPostView.as_view(), name="journal_post"),
    path("journal/<int:pk>/delete/", views.JournalDeleteView.as_view(), name="journal_delete"),
    path("journal/<int:pk>/reverse/", views.JournalReverseView.as_view(), name="journal_reverse"),
]
