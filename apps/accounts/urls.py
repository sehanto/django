from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.LoginView.as_view(), name="login"),
    path("logout/", views.LogoutView.as_view(), name="logout"),
    path("login/verify/", views.LoginOTPView.as_view(), name="login_otp"),
    path("security/", views.SecurityView.as_view(), name="security"),
    path("security/two-factor/setup/", views.TwoFactorSetupView.as_view(), name="two_factor_setup"),
    path("security/two-factor/backup-codes/", views.BackupCodesView.as_view(), name="two_factor_backup_codes"),
    path("security/two-factor/disable/", views.TwoFactorDisableView.as_view(), name="two_factor_disable"),
    path("security/two-factor/verify/", views.TwoFactorVerifySessionView.as_view(), name="two_factor_verify_session"),
    path("signup/", views.SignUpView.as_view(), name="signup"),
    path("profile/", views.ProfileView.as_view(), name="profile"),
    path("password/", views.PasswordChangeView.as_view(), name="password_change"),
    path("language/", views.SetLanguageView.as_view(), name="set_language"),
    path("verify-email/", views.VerifyEmailPendingView.as_view(), name="verify_email_pending"),
    path("verify-email/resend/", views.ResendVerificationView.as_view(), name="verify_email_resend"),
    path("verify-email/change/", views.ChangeEmailView.as_view(), name="verify_email_change"),
    path("verify-email/<str:token>/", views.VerifyEmailView.as_view(), name="verify_email"),
    path("password-reset/", views.PasswordResetView.as_view(), name="password_reset"),
    path("password-reset/sent/", views.PasswordResetDoneView.as_view(), name="password_reset_done"),
    path("password-reset/complete/", views.PasswordResetCompleteView.as_view(), name="password_reset_complete"),
    path("password-reset/<uidb64>/<token>/", views.PasswordResetConfirmView.as_view(), name="password_reset_confirm"),
]
