import logging
from datetime import timedelta
from urllib.parse import urlsplit

from axes.utils import reset as axes_reset
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth import views as auth_views
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.utils import translation
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.views import View
from django.views.generic import FormView, TemplateView, UpdateView

from apps.core.exceptions import DomainError
from apps.tenants.services import register_owner_with_organization
from apps.tenants.session import set_current_organization

from . import services, throttle
from .client_ip import client_ip
from .forms import ChangeEmailForm, LoginForm, NewPasswordForm, ProfileForm, ResetPasswordForm, SignUpForm
from .middleware import SUPPORTED_LANGUAGES

logger = logging.getLogger(__name__)


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
        services.send_verification_email(user)
        return redirect("accounts:verify_email_pending")


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


# --- Password reset by email -----------------------------------------------------------


def _reset_link_lifetime():
    return {"link_lifetime": timedelta(seconds=settings.PASSWORD_RESET_TIMEOUT)}


class PasswordResetView(auth_views.PasswordResetView):
    """Request a reset link. Always ends on the same "check your email" page, whether or
    not the address has an account or the request was throttled, so nothing leaks."""

    template_name = "accounts/password_reset_form.html"
    form_class = ResetPasswordForm
    email_template_name = "accounts/emails/password_reset.txt"
    subject_template_name = "accounts/emails/password_reset_subject.txt"
    success_url = reverse_lazy("accounts:password_reset_done")

    def form_valid(self, form):
        email = form.cleaned_data["email"].lower()
        within_limits = all([
            throttle.allow("password-reset-email", email, limit=settings.PASSWORD_RESET_EMAILS_PER_ADDRESS_PER_HOUR, window_seconds=3600),
            throttle.allow("password-reset-ip", client_ip(self.request) or "unknown",
                           limit=settings.PASSWORD_RESET_REQUESTS_PER_IP_PER_HOUR, window_seconds=3600),
        ])
        if not within_limits:
            logger.warning("Password reset throttled for a request from %s", client_ip(self.request))
            return redirect(self.success_url)
        # Build the link from SITE_URL, never from the request's Host header (which a client controls).
        site = urlsplit(settings.SITE_URL)
        form.save(
            domain_override=site.netloc,
            use_https=site.scheme == "https",
            request=self.request,
            from_email=settings.DEFAULT_FROM_EMAIL,
            subject_template_name=self.subject_template_name,
            email_template_name=self.email_template_name,
            token_generator=self.token_generator,
            extra_email_context={"site_name": settings.SITE_NAME, **_reset_link_lifetime()},
        )
        return redirect(self.success_url)


class PasswordResetDoneView(auth_views.PasswordResetDoneView):
    template_name = "accounts/password_reset_done.html"

    def get_context_data(self, **kwargs):
        return super().get_context_data(**_reset_link_lifetime(), **kwargs)


class PasswordResetConfirmView(auth_views.PasswordResetConfirmView):
    template_name = "accounts/password_reset_confirm.html"
    form_class = NewPasswordForm
    success_url = reverse_lazy("accounts:password_reset_complete")

    def form_valid(self, form):
        response = super().form_valid(form)
        # Proving control of the mailbox lifts any login lockout and confirms the address.
        axes_reset(username=self.user.email.lower())
        services.mark_email_verified(self.user)
        return response


class PasswordResetCompleteView(auth_views.PasswordResetCompleteView):
    template_name = "accounts/password_reset_complete.html"


# --- Email verification ----------------------------------------------------------------


def _pending_context(request, change_form=None) -> dict:
    return {
        "change_form": change_form or ChangeEmailForm(user=request.user),
        "lifetime": timedelta(seconds=settings.EMAIL_VERIFICATION_TIMEOUT),
    }


class VerifyEmailPendingView(LoginRequiredMixin, TemplateView):
    """Where unverified users land: resend the link or fix a mistyped address."""

    template_name = "accounts/verify_email_pending.html"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and request.user.is_email_verified:
            return redirect("dashboard:home")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        return super().get_context_data(**_pending_context(self.request), **kwargs)


class ResendVerificationView(LoginRequiredMixin, View):
    def post(self, request):
        if not request.user.is_email_verified:
            if services.send_verification_email(request.user):
                messages.success(request, _("We sent a new confirmation link to %(email)s.") % {"email": request.user.email})
            else:
                messages.error(request, _("Too many emails were requested. Please wait an hour and try again."))
        return redirect("accounts:verify_email_pending")


class ChangeEmailView(LoginRequiredMixin, View):
    def post(self, request):
        form = ChangeEmailForm(request.POST, user=request.user)
        if form.is_valid():
            try:
                services.change_unverified_email(user=request.user, new_email=form.cleaned_data["email"])
                messages.success(request, _("Email address updated. We sent a confirmation link to %(email)s.") % {"email": request.user.email})
                return redirect("accounts:verify_email_pending")
            except DomainError as exc:
                form.add_error("email", exc.message)
        return render(request, VerifyEmailPendingView.template_name, _pending_context(request, form))


class VerifyEmailView(View):
    """Target of the emailed link. Works whether or not the person is signed in."""

    def get(self, request, token):
        try:
            user = services.verify_email(token)
        except DomainError as exc:
            messages.error(request, exc.message)
            return redirect("accounts:verify_email_pending" if request.user.is_authenticated else "accounts:login")
        if request.user.is_authenticated and request.user.pk == user.pk:
            messages.success(request, _("Email address confirmed. Welcome! A standard SAK EMKM chart of accounts has been set up."))
            return redirect("tenants:settings")
        messages.success(request, _("Email address confirmed. You can sign in now."))
        # Someone else may be signed in on this browser; don't switch accounts.
        return redirect("dashboard:home" if request.user.is_authenticated else "accounts:login")
