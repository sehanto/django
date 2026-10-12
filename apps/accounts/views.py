from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth import views as auth_views
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.utils import translation
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.views import View
from django.views.generic import FormView, UpdateView

from apps.tenants.services import register_owner_with_organization
from apps.tenants.session import set_current_organization

from .forms import LoginForm, ProfileForm, SignUpForm
from .middleware import SUPPORTED_LANGUAGES


def remember_language(response, request, language: str) -> None:
    """Store the language in the cookie used for signed-out visitors."""
    response.set_cookie(
        settings.LANGUAGE_COOKIE_NAME, language,
        max_age=settings.LANGUAGE_COOKIE_AGE, samesite="Lax", secure=request.is_secure(),
    )


class LoginView(auth_views.LoginView):
    template_name = "accounts/login.html"
    authentication_form = LoginForm
    redirect_authenticated_user = True

    def form_valid(self, form):
        response = super().form_valid(form)
        # A language picked on the sign-in page is an explicit choice: keep it after login.
        chosen = self.request.COOKIES.get(settings.LANGUAGE_COOKIE_NAME)
        user = self.request.user
        if chosen in SUPPORTED_LANGUAGES and chosen != user.language:
            user.language = chosen
            user.save(update_fields=["language"])
        return response


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
            language=self.request.LANGUAGE_CODE,
        )
        login(self.request, user, backend="django.contrib.auth.backends.ModelBackend")
        set_current_organization(self.request, organization)
        messages.success(self.request, _("Welcome! A standard SAK EMKM chart of accounts has been set up."))
        return redirect("tenants:settings")


class ProfileView(LoginRequiredMixin, UpdateView):
    template_name = "accounts/profile.html"
    form_class = ProfileForm
    success_url = reverse_lazy("accounts:profile")

    def get_object(self, queryset=None):
        return self.request.user

    def form_valid(self, form):
        response = super().form_valid(form)
        # Apply the (possibly changed) language right away, including for the flash message.
        translation.activate(self.object.language)
        remember_language(response, self.request, self.object.language)
        messages.success(self.request, _("Profile updated."))
        return response


class PasswordChangeView(auth_views.PasswordChangeView):
    template_name = "accounts/password_change.html"
    success_url = reverse_lazy("accounts:profile")

    def form_valid(self, form):
        messages.success(self.request, _("Password changed successfully."))
        return super().form_valid(form)


class SetLanguageView(View):
    """Switch the interface language: saved on the account and in a cookie."""

    def post(self, request):
        language = request.POST.get("language")
        next_url = request.POST.get("next") or "/"
        if not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
            next_url = "/"
        response = redirect(next_url)
        if language not in SUPPORTED_LANGUAGES:
            return response
        if request.user.is_authenticated:
            request.user.language = language
            request.user.save(update_fields=["language"])
        remember_language(response, request, language)
        return response
