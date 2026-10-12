from django import forms
from django.contrib.auth import password_validation
from django.contrib.auth.forms import AuthenticationForm
from django.utils.translation import gettext_lazy as _

from apps.core.forms import BaseForm, BaseModelForm, SanitizedFormMixin
from apps.tenants.models import Organization

from .models import User


class LoginForm(SanitizedFormMixin, AuthenticationForm):
    username = forms.EmailField(label=_("Email"), widget=forms.EmailInput(attrs={"autofocus": True}))


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
