from django.urls import path

from . import views

app_name = "ledger"

urlpatterns = [
    path("akun/", views.AccountListView.as_view(), name="accounts"),
    path("akun/baru/", views.AccountCreateView.as_view(), name="account_create"),
    path("akun/<int:pk>/ubah/", views.AccountUpdateView.as_view(), name="account_update"),
    path("akun/<int:pk>/hapus/", views.AccountDeleteView.as_view(), name="account_delete"),
    path("jurnal/", views.JournalListView.as_view(), name="journals"),
    path("jurnal/baru/", views.JournalCreateView.as_view(), name="journal_create"),
    path("jurnal/<int:pk>/", views.JournalDetailView.as_view(), name="journal_detail"),
    path("jurnal/<int:pk>/ubah/", views.JournalUpdateView.as_view(), name="journal_update"),
    path("jurnal/<int:pk>/posting/", views.JournalPostView.as_view(), name="journal_post"),
    path("jurnal/<int:pk>/hapus/", views.JournalDeleteView.as_view(), name="journal_delete"),
    path("jurnal/<int:pk>/balik/", views.JournalReverseView.as_view(), name="journal_reverse"),
]
