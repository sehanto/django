from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("masuk/", views.LoginView.as_view(), name="login"),
    path("keluar/", views.LogoutView.as_view(), name="logout"),
    path("daftar/", views.SignUpView.as_view(), name="signup"),
    path("profil/", views.ProfileView.as_view(), name="profile"),
    path("kata-sandi/", views.PasswordChangeView.as_view(), name="password_change"),
    path("bahasa/", views.SetLanguageView.as_view(), name="set_language"),
]
