from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.LoginView.as_view(), name="login"),
    path("logout/", views.LogoutView.as_view(), name="logout"),
    path("signup/", views.SignUpView.as_view(), name="signup"),
    path("profile/", views.ProfileView.as_view(), name="profile"),
    path("password/", views.PasswordChangeView.as_view(), name="password_change"),
    path("language/", views.SetLanguageView.as_view(), name="set_language"),
    path("password-reset/", views.PasswordResetView.as_view(), name="password_reset"),
    path("password-reset/sent/", views.PasswordResetDoneView.as_view(), name="password_reset_done"),
    path("password-reset/complete/", views.PasswordResetCompleteView.as_view(), name="password_reset_complete"),
    path("password-reset/<uidb64>/<token>/", views.PasswordResetConfirmView.as_view(), name="password_reset_confirm"),
]
