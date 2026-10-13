from django import forms
from django.forms.utils import ErrorDict
from django.conf import settings
from django.contrib.auth import password_validation
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm, PasswordResetForm, SetPasswordForm
from django.utils import translation
from django.utils.translation import gettext_lazy as _

from apps.core.forms import BaseForm, BaseModelForm, SanitizedFormMixin
from apps.tenants.models import Organization

from . import captcha
from .models import User


class LoginForm(SanitizedFormMixin, AuthenticationForm):
    username = forms.EmailField(label=_("Email"), widget=forms.EmailInput(attrs={"autofocus": True}))
    error_messages = {
        **AuthenticationForm.error_messages,
        "captcha": _("Please complete the security check, then sign in again."),
    }

    def clean(self):
        # After repeated failures, the password is only checked once the CAPTCHA is solved.
        if captcha.is_required(self.request, self.data.get("username")) and not captcha.verify(self.request, captcha.CaptchaAction.LOGIN):
            raise forms.ValidationError(self.error_messages["captcha"], code="captcha")
        return super().clean()

    def clean_username(self):
        return self.cleaned_data["username"].lower()  # accounts are stored with lowercase emails


class CaptchaFirstFormMixin:
    """The CAPTCHA comes first and alone: without a solved one, no field is validated and
    nothing else is said (no "already registered", no throttle counted, no email sent)."""

    captcha_action: captcha.CaptchaAction

    def __init__(self, *args, request=None, **kwargs):
        self.request = request
        super().__init__(*args, **kwargs)

    def full_clean(self):
        if self.is_bound and not captcha.verify(self.request, self.captcha_action):
            self._errors = ErrorDict()
            self.cleaned_data = {}
            self.add_error(None, forms.ValidationError(
                _("Please complete the security check, then submit the form again."), code="captcha"
            ))
            return
        super().full_clean()


class SignUpForm(CaptchaFirstFormMixin, BaseForm):
    """Registers an owner together with their first organization."""

    captcha_action = captcha.CaptchaAction.SIGNUP

    full_name = forms.CharField(label=_("Full name"), max_length=150)
    email = forms.EmailField(label=_("Email"))
    # strip=False: the login form uses the password exactly as typed, so sign-up must too.
    password = forms.CharField(label=_("Password"), widget=forms.PasswordInput, strip=False)
    organization_name = forms.CharField(label=_("Business name"), max_length=200)
    legal_form = forms.ChoiceField(label=_("Legal form"), choices=Organization.LegalForm.choices)

    def clean_email(self):
        email = self.cleaned_data["email"].lower()
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError(_("This email is already registered."))
        return email

    def clean_password(self):
        password = self.cleaned_data["password"]
        password_validation.validate_password(password)
        return password


class ProfileForm(BaseModelForm):
    class Meta:
        model = User
        fields = ["full_name", "phone", "language"]


class ResetPasswordForm(CaptchaFirstFormMixin, SanitizedFormMixin, PasswordResetForm):
    """Django's reset form; the email goes out in the recipient's own language."""

    captcha_action = captcha.CaptchaAction.PASSWORD_RESET

    email = forms.EmailField(label=_("Email"), max_length=254, widget=forms.EmailInput(attrs={"autocomplete": "email", "autofocus": True}))

    def send_mail(self, subject_template_name, email_template_name, context, *args, **kwargs):
        with translation.override(context["user"].language):
            super().send_mail(subject_template_name, email_template_name, context, *args, **kwargs)


class ChangePasswordForm(CaptchaFirstFormMixin, SanitizedFormMixin, PasswordChangeForm):
    """Change the password while signed in. The CAPTCHA comes first, so a hijacked session
    can't be used by a script to guess the current password."""

    captcha_action = captcha.CaptchaAction.CHANGE_PASSWORD


class NewPasswordForm(SanitizedFormMixin, SetPasswordForm):
    """Choose a new password (passwords are never sanitized; Django uses ``strip=False``)."""


class ChangeEmailForm(CaptchaFirstFormMixin, BaseForm):
    """Correct a mistyped address before it is confirmed (requires the current password).
    The CAPTCHA comes first, so the form can't be used to test passwords or to email strangers."""

    captcha_action = captcha.CaptchaAction.CHANGE_EMAIL

    email = forms.EmailField(label=_("Correct email address"), max_length=254)
    password = forms.CharField(label=_("Current password"), widget=forms.PasswordInput, strip=False)

    def __init__(self, *args, user, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_password(self):
        password = self.cleaned_data["password"]
        if not self.user.check_password(password):
            raise forms.ValidationError(_("Your password is incorrect."))
        return password


def two_factor_code_field(*, autofocus: bool) -> forms.CharField:
    attrs = {"autocomplete": "one-time-code", "autocapitalize": "off"}
    if autofocus:
        attrs["autofocus"] = True
    return forms.CharField(
        label=_("Authentication code"), max_length=20,
        help_text=_("The 6-digit code from your authenticator app, or one of your backup codes."),
        widget=forms.TextInput(attrs=attrs),
    )


class TwoFactorCodeForm(BaseForm):
    code = two_factor_code_field(autofocus=True)


class VerifySessionForm(CaptchaFirstFormMixin, TwoFactorCodeForm):
    """Step-up for a signed-in session; the CAPTCHA first, so a script can't try codes."""

    captcha_action = captcha.CaptchaAction.VERIFY_SESSION


class TwoFactorLoginForm(TwoFactorCodeForm):
    """Sign-in code step, with the optional "remember this device"."""

    def __init__(self, *args, offer_remember: bool, **kwargs):
        super().__init__(*args, **kwargs)
        if offer_remember:
            self.fields["remember"] = forms.BooleanField(
                label=_("Don't ask for a code on this device for %(days)s days") % {"days": settings.TWO_FACTOR_REMEMBER_DAYS},
                help_text=_("Only on your own device, never on a shared or public computer."),
                required=False,
            )


class PasswordConfirmationForm(BaseForm):
    """Re-authentication before a security change."""

    password = forms.CharField(label=_("Current password"), widget=forms.PasswordInput, strip=False)

    def __init__(self, *args, user, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_password(self):
        password = self.cleaned_data["password"]
        if not self.user.check_password(password):
            raise forms.ValidationError(_("Your password is incorrect."))
        return password


class TwoFactorConfirmForm(PasswordConfirmationForm):
    """Password plus a current code (used to turn 2FA on or off)."""

    code = two_factor_code_field(autofocus=False)  # the password field comes first


class RegenerateBackupCodesForm(CaptchaFirstFormMixin, PasswordConfirmationForm):
    """Password before new backup codes; the CAPTCHA first, so the password can't be guessed here."""

    captcha_action = captcha.CaptchaAction.BACKUP_CODES


class EnableTwoFactorForm(CaptchaFirstFormMixin, TwoFactorConfirmForm):
    """Password and the first code from the app; the CAPTCHA first, so neither is checked
    for a script using a hijacked session."""

    captcha_action = captcha.CaptchaAction.ENABLE_TWO_FACTOR


class DisableTwoFactorForm(CaptchaFirstFormMixin, TwoFactorConfirmForm):
    """Password and code before 2FA goes off; the CAPTCHA first, so neither is checked (nor a
    code attempt counted) for a script using a hijacked session."""

    captcha_action = captcha.CaptchaAction.DISABLE_TWO_FACTOR
