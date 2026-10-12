from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth import views as auth_views
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.views.generic import FormView, UpdateView

from apps.tenants.services import register_owner_with_organization
from apps.tenants.session import set_current_organization

from .forms import LoginForm, ProfileForm, SignUpForm


class LoginView(auth_views.LoginView):
    template_name = "accounts/login.html"
    authentication_form = LoginForm
    redirect_authenticated_user = True


class LogoutView(auth_views.LogoutView):
    pass


class SignUpView(FormView):
    template_name = "accounts/signup.html"
    form_class = SignUpForm

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect("dashboard:home")
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        data = form.cleaned_data
        user, organization = register_owner_with_organization(
            email=data["email"],
            password=data["password"],
            full_name=data["full_name"],
            organization_name=data["organization_name"],
            legal_form=data["legal_form"],
        )
        login(self.request, user, backend="django.contrib.auth.backends.ModelBackend")
        set_current_organization(self.request, organization)
        messages.success(self.request, "Selamat datang! Bagan akun standar SAK EMKM telah disiapkan.")
        return redirect("tenants:settings")


class ProfileView(LoginRequiredMixin, UpdateView):
    template_name = "accounts/profile.html"
    form_class = ProfileForm
    success_url = reverse_lazy("accounts:profile")

    def get_object(self, queryset=None):
        return self.request.user

    def form_valid(self, form):
        messages.success(self.request, "Profil diperbarui.")
        return super().form_valid(form)


class PasswordChangeView(auth_views.PasswordChangeView):
    template_name = "accounts/password_change.html"
    success_url = reverse_lazy("accounts:profile")

    def form_valid(self, form):
        messages.success(self.request, "Kata sandi berhasil diubah.")
        return super().form_valid(form)
