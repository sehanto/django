import logging
from datetime import timedelta
from urllib.parse import urlsplit

from axes.utils import reset as axes_reset
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django_otp import login as otp_login
from django.contrib.auth import views as auth_views
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import translation
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.utils.translation import ngettext
from django.views import View
from django.views.generic import FormView, TemplateView, UpdateView

from apps.audit import services as audit
from apps.audit.domain import AuditAction
from apps.audit.selectors import account_events
from apps.core.exceptions import DomainError
from apps.core.params import int_param
from apps.tenants.selectors import organizations_requiring_two_factor
from apps.tenants.services import register_owner_with_organization
from apps.tenants.session import set_current_organization

from . import captcha, services, throttle, trusted_devices, two_factor
from .client_ip import client_ip
from .forms import (
    ChangeEmailForm,
    ChangePasswordForm,
    LoginForm,
    NewPasswordForm,
    PasswordConfirmationForm,
    ProfileForm,
    ResetPasswordForm,
    SignUpForm,
    TwoFactorCodeForm,
    TwoFactorConfirmForm,
    TwoFactorLoginForm,
)
from .middleware import SUPPORTED_LANGUAGES
from .models import BackupCodeDevice

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

    def get_context_data(self, **kwargs):
        # After a failed POST the failure is already counted, so the widget appears right away.
        email = self.request.POST.get("username", "") if self.request.method == "POST" else ""
        return super().get_context_data(captcha=captcha.login_widget(self.request, email), **kwargs)

    def form_valid(self, form):
        user = form.get_user()
        remembered = two_factor.is_enabled(user) and trusted_devices.recognized(self.request, user)
        if two_factor.is_enabled(user) and not remembered:
            # Password accepted; the user is not logged in until the second factor is checked.
            two_factor.begin_login(self.request, user, next_url=self.get_success_url())
            return redirect("accounts:login_otp")
        response = super().form_valid(form)
        if remembered:
            otp_login(self.request, two_factor.authenticator(user))  # this browser proved the code earlier
        adopt_chosen_language(self.request)
        return response


def adopt_chosen_language(request) -> None:
    """A language picked on the sign-in page is an explicit choice: keep it after login."""
    chosen = request.COOKIES.get(settings.LANGUAGE_COOKIE_NAME)
    user = request.user
    if chosen in SUPPORTED_LANGUAGES and chosen != user.language:
        user.language = chosen
        user.save(update_fields=["language"])


class LogoutView(auth_views.LogoutView):
    pass


class SignUpView(FormView):
    template_name = "accounts/signup.html"
    form_class = SignUpForm

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "request": self.request}

    def get_context_data(self, **kwargs):
        return super().get_context_data(captcha=captcha.widget(captcha.CaptchaAction.SIGNUP), **kwargs)

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
    form_class = ChangePasswordForm
    success_url = reverse_lazy("accounts:profile")

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "request": self.request}

    def get_context_data(self, **kwargs):
        return super().get_context_data(captcha=captcha.widget(captcha.CaptchaAction.CHANGE_PASSWORD), **kwargs)

    def form_valid(self, form):
        response = super().form_valid(form)
        audit.record(AuditAction.PASSWORD_CHANGED, request=self.request)
        messages.success(self.request, _("Password changed successfully."))
        return response


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

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "request": self.request}

    def get_context_data(self, **kwargs):
        return super().get_context_data(captcha=captcha.widget(captcha.CaptchaAction.PASSWORD_RESET), **kwargs)

    def form_valid(self, form):
        email = form.cleaned_data["email"].lower()
        within_limits = all([
            throttle.allow(throttle.ThrottleScope.PASSWORD_RESET_EMAIL, email, limit=settings.PASSWORD_RESET_EMAILS_PER_ADDRESS_PER_HOUR, window_seconds=3600),
            throttle.allow(throttle.ThrottleScope.PASSWORD_RESET_IP, client_ip(self.request) or "unknown",
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
        captcha.record_success(self.user.email)
        services.mark_email_verified(self.user)
        audit.record(AuditAction.PASSWORD_RESET, request=self.request, actor=self.user)
        return response


class PasswordResetCompleteView(auth_views.PasswordResetCompleteView):
    template_name = "accounts/password_reset_complete.html"


# --- Email verification ----------------------------------------------------------------


def _pending_context(request, change_form=None) -> dict:
    return {
        "change_form": change_form or ChangeEmailForm(user=request.user),
        "lifetime": timedelta(seconds=settings.EMAIL_VERIFICATION_TIMEOUT),
        "captcha": captcha.widget(captcha.CaptchaAction.RESEND_VERIFICATION),
        "change_captcha": captcha.widget(captcha.CaptchaAction.CHANGE_EMAIL),
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
        if request.user.is_email_verified:
            return redirect("accounts:verify_email_pending")
        if not captcha.verify(request, captcha.CaptchaAction.RESEND_VERIFICATION):
            # Nothing sent, and the hourly email limit is not used up.
            messages.error(request, _("Please complete the security check, then submit the form again."))
        elif services.send_verification_email(request.user):
            messages.success(request, _("We sent a new confirmation link to %(email)s.") % {"email": request.user.email})
        else:
            messages.error(request, _("Too many emails were requested. Please wait an hour and try again."))
        return redirect("accounts:verify_email_pending")


class ChangeEmailView(LoginRequiredMixin, View):
    def post(self, request):
        form = ChangeEmailForm(request.POST, user=request.user, request=request)
        if form.is_valid():
            try:
                previous = request.user.email
                services.change_unverified_email(user=request.user, new_email=form.cleaned_data["email"])
                audit.record(AuditAction.EMAIL_CHANGED, request=request, target=request.user.email, previous=previous)
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


# --- Two-factor authentication ---------------------------------------------------------


def _wait_message(seconds: int) -> str:
    return ngettext(
        "Too many wrong codes. Wait %(seconds)s second and try again.",
        "Too many wrong codes. Wait %(seconds)s seconds and try again.",
        seconds,
    ) % {"seconds": seconds}


class LoginOTPView(FormView):
    """Second sign-in step: authenticator code or backup code."""

    template_name = "accounts/login_otp.html"
    form_class = TwoFactorLoginForm

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "offer_remember": trusted_devices.is_offered(self.user)}

    def dispatch(self, request, *args, **kwargs):
        self.pending = two_factor.pending_login(request)
        self.user = two_factor.pending_user(self.pending) if self.pending else None
        if self.user is None:
            messages.info(request, _("Please sign in again."))
            return redirect("accounts:login")
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        if wait := two_factor.retry_after(two_factor.login_devices(self.user)):
            form.add_error("code", _wait_message(wait))  # refused before checking: not counted as an attempt
            return self.form_invalid(form)
        device = two_factor.verify(self.user, form.cleaned_data["code"])
        if device is None:
            audit.record(AuditAction.LOGIN_FAILED, request=self.request, actor=self.user, target=self.user.email, step="two_factor")
            if two_factor.record_failed_attempt(self.request):
                form.add_error("code", _("That code is not valid. Check your authenticator app and try again."))
                return self.form_invalid(form)
            messages.error(self.request, _("Too many wrong codes. Please sign in again."))
            return redirect("accounts:login")

        two_factor.cancel_login(self.request)
        login(self.request, self.user, backend=self.pending["backend"])
        otp_login(self.request, device)  # marks this session as verified (needed for the admin)
        adopt_chosen_language(self.request)
        response = redirect(self.pending["next"])
        if form.cleaned_data.get("remember"):
            remembered = trusted_devices.remember(self.request, response, self.user)
            audit.record(AuditAction.DEVICE_REMEMBERED, request=self.request, target=remembered.label)
            services.send_security_notice(
                self.user, AuditAction.DEVICE_REMEMBERED, days=settings.TWO_FACTOR_REMEMBER_DAYS, device=remembered.label
            )
        if isinstance(device, BackupCodeDevice):
            audit.record(AuditAction.BACKUP_CODE_USED, request=self.request)
            left = two_factor.remaining_backup_codes(self.user)
            messages.warning(self.request, ngettext(
                "You signed in with a backup code. %(count)s backup code is left.",
                "You signed in with a backup code. %(count)s backup codes are left.",
                left,
            ) % {"count": left})
        return response


BACKUP_CODES_SESSION_KEY = "two_factor_new_backup_codes"  # shown once, then removed


class SecurityView(LoginRequiredMixin, TemplateView):
    template_name = "accounts/security.html"
    RECENT_ACTIVITY = 15

    def get_context_data(self, **kwargs):
        user = self.request.user
        return super().get_context_data(
            two_factor_enabled=two_factor.is_enabled(user),
            backup_codes_left=two_factor.remaining_backup_codes(user),
            required_by=list(organizations_requiring_two_factor(user)),
            trusted_devices=trusted_devices.devices_for(user),
            this_device_id=trusted_devices.current_device_id(self.request),
            recent_activity=account_events(user)[: self.RECENT_ACTIVITY],
            **kwargs,
        )


class ForgetTrustedDeviceView(LoginRequiredMixin, View):
    def post(self, request):
        device_id = int_param(request.POST, "device", minimum=1)
        count = trusted_devices.forget(request.user, device_id)  # None = all devices
        if count:
            audit.record(AuditAction.DEVICES_FORGOTTEN, request=request, count=count)
        messages.success(request, ngettext(
            "%(count)s remembered device forgotten. It will be asked for a code at the next sign-in.",
            "%(count)s remembered devices forgotten. They will be asked for a code at the next sign-in.",
            count,
        ) % {"count": count})
        return redirect("accounts:security")


class TwoFactorVerifySessionView(LoginRequiredMixin, FormView):
    """Step-up: an already signed-in session proves the second factor (e.g. it began
    before the user turned 2FA on, and an organization now requires it)."""

    template_name = "accounts/two_factor_verify_session.html"
    form_class = TwoFactorCodeForm

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not two_factor.is_enabled(request.user):
            return redirect("accounts:security")
        if request.user.is_authenticated and request.user.is_verified():
            return redirect(self.get_success_url())
        return super().dispatch(request, *args, **kwargs)

    def get_success_url(self):
        next_url = self.request.POST.get("next") or self.request.GET.get("next") or ""
        if url_has_allowed_host_and_scheme(next_url, allowed_hosts={self.request.get_host()}, require_https=self.request.is_secure()):
            return next_url
        return reverse("dashboard:home")

    def get_context_data(self, **kwargs):
        return super().get_context_data(next=self.request.GET.get("next", ""), **kwargs)

    def form_valid(self, form):
        user = self.request.user
        if wait := two_factor.retry_after(two_factor.login_devices(user)):
            form.add_error("code", _wait_message(wait))
            return self.form_invalid(form)
        device = two_factor.verify(user, form.cleaned_data["code"])
        if device is None:
            form.add_error("code", _("That code is not valid. Check your authenticator app and try again."))
            return self.form_invalid(form)
        otp_login(self.request, device)
        return redirect(self.get_success_url())


class TwoFactorSetupView(LoginRequiredMixin, FormView):
    template_name = "accounts/two_factor_setup.html"
    form_class = TwoFactorConfirmForm

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and two_factor.is_enabled(request.user):
            return redirect("accounts:security")
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "user": self.request.user}

    def get_context_data(self, **kwargs):
        device = two_factor.start_setup(self.request.user)
        return super().get_context_data(
            qr_code=two_factor.qr_code_data_uri(device), manual_key=two_factor.manual_entry_key(device), **kwargs
        )

    def form_valid(self, form):
        user = self.request.user
        device = two_factor.start_setup(user)
        if wait := two_factor.retry_after([device]):
            form.add_error("code", _wait_message(wait))
            return self.form_invalid(form)
        codes = two_factor.confirm_setup(user, device, form.cleaned_data["code"])
        if codes is None:
            form.add_error("code", _("That code is not valid. Check that the time on your phone is correct and try again."))
            return self.form_invalid(form)
        otp_login(self.request, two_factor.authenticator(user))  # this session already proved the code
        audit.record(AuditAction.TWO_FACTOR_ENABLED, request=self.request)
        services.send_security_notice(user, AuditAction.TWO_FACTOR_ENABLED)
        self.request.session[BACKUP_CODES_SESSION_KEY] = codes
        messages.success(self.request, _("Two-factor authentication is on."))
        return redirect("accounts:two_factor_backup_codes")


class BackupCodesView(LoginRequiredMixin, FormView):
    """Shows newly generated backup codes once; regenerating needs the password."""

    template_name = "accounts/two_factor_backup_codes.html"
    form_class = PasswordConfirmationForm

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not two_factor.is_enabled(request.user):
            return redirect("accounts:security")
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "user": self.request.user}

    def get_context_data(self, **kwargs):
        return super().get_context_data(
            new_codes=self.request.session.pop(BACKUP_CODES_SESSION_KEY, None),
            backup_codes_left=two_factor.remaining_backup_codes(self.request.user),
            **kwargs,
        )

    def form_valid(self, form):
        self.request.session[BACKUP_CODES_SESSION_KEY] = two_factor.generate_backup_codes(self.request.user)
        audit.record(AuditAction.BACKUP_CODES_REGENERATED, request=self.request)
        services.send_security_notice(self.request.user, AuditAction.BACKUP_CODES_REGENERATED)
        messages.success(self.request, _("New backup codes created. The old ones no longer work."))
        return redirect("accounts:two_factor_backup_codes")


class TwoFactorDisableView(LoginRequiredMixin, FormView):
    template_name = "accounts/two_factor_disable.html"
    form_class = TwoFactorConfirmForm

    def get_context_data(self, **kwargs):
        return super().get_context_data(required_by=list(organizations_requiring_two_factor(self.request.user)), **kwargs)

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not two_factor.is_enabled(request.user):
            return redirect("accounts:security")
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "user": self.request.user}

    def form_valid(self, form):
        user = self.request.user
        if required_by := list(organizations_requiring_two_factor(user)):
            form.add_error(None, _("You can't turn off two-factor authentication while %(organizations)s requires it.") % {
                "organizations": ", ".join(org.name for org in required_by),
            })
            return self.form_invalid(form)
        if wait := two_factor.retry_after(two_factor.login_devices(user)):
            form.add_error("code", _wait_message(wait))
            return self.form_invalid(form)
        if two_factor.verify(user, form.cleaned_data["code"]) is None:
            form.add_error("code", _("That code is not valid. Check your authenticator app and try again."))
            return self.form_invalid(form)
        two_factor.disable(user)
        trusted_devices.forget(user)
        audit.record(AuditAction.TWO_FACTOR_DISABLED, request=self.request)
        services.send_security_notice(user, AuditAction.TWO_FACTOR_DISABLED)
        messages.warning(self.request, _("Two-factor authentication is off. Your account is protected by your password only."))
        return redirect("accounts:security")
