from django import forms
from django.conf import settings
from django.contrib.auth import password_validation
from django.contrib.auth.forms import AuthenticationForm, PasswordResetForm, SetPasswordForm
from django.utils import translation
from django.utils.translation import gettext_lazy as _

from apps.core.forms import BaseForm, BaseModelForm, SanitizedFormMixin
from apps.tenants.models import Organization

from .models import User


class LoginForm(SanitizedFormMixin, AuthenticationForm):
    username = forms.EmailField(label=_("Email"), widget=forms.EmailInput(attrs={"autofocus": True}))

    def clean_username(self):
        return self.cleaned_data["username"].lower()  # accounts are stored with lowercase emails


class SignUpForm(BaseForm):
    """Registers an owner together with their first organization."""

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


class ResetPasswordForm(SanitizedFormMixin, PasswordResetForm):
    """Django's reset form; the email goes out in the recipient's own language."""

    email = forms.EmailField(label=_("Email"), max_length=254, widget=forms.EmailInput(attrs={"autocomplete": "email", "autofocus": True}))

    def send_mail(self, subject_template_name, email_template_name, context, *args, **kwargs):
        with translation.override(context["user"].language):
            super().send_mail(subject_template_name, email_template_name, context, *args, **kwargs)


class NewPasswordForm(SanitizedFormMixin, SetPasswordForm):
    """Choose a new password (passwords are never sanitized; Django uses ``strip=False``)."""


class ChangeEmailForm(BaseForm):
    """Correct a mistyped address before it is confirmed (requires the current password)."""

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
