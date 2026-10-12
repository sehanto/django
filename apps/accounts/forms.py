from django import forms
from django.contrib.auth import password_validation
from django.contrib.auth.forms import AuthenticationForm

from apps.tenants.models import Organization

from .models import User


class LoginForm(AuthenticationForm):
    username = forms.EmailField(label="Email", widget=forms.EmailInput(attrs={"autofocus": True}))


class SignUpForm(forms.Form):
    """Registers an owner together with their first organization."""

    full_name = forms.CharField(label="Nama lengkap", max_length=150)
    email = forms.EmailField(label="Email")
    password = forms.CharField(label="Kata sandi", widget=forms.PasswordInput)
    organization_name = forms.CharField(label="Nama usaha", max_length=200)
    legal_form = forms.ChoiceField(label="Bentuk usaha", choices=Organization.LegalForm.choices)

    def clean_email(self):
        email = self.cleaned_data["email"].lower()
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError("Email sudah terdaftar.")
        return email

    def clean_password(self):
        password = self.cleaned_data["password"]
        password_validation.validate_password(password)
        return password


class ProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ["full_name", "phone"]
