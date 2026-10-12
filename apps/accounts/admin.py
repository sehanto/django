from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.utils.translation import gettext_lazy as _
from django_otp.plugins.otp_static.models import StaticDevice
from django_otp.plugins.otp_totp.models import TOTPDevice

from .models import AuthenticatorDevice, BackupCodeDevice, TrustedDevice, User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    ordering = ("email",)
    list_display = ("email", "full_name", "is_active", "email_verified_at", "is_staff", "date_joined")
    list_filter = ("is_active", "is_staff", "is_superuser", ("email_verified_at", admin.EmptyFieldListFilter))
    search_fields = ("email", "full_name")
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        (_("Profile"), {"fields": ("full_name", "phone", "language")}),
        (_("Permissions"), {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        (_("Important dates"), {"fields": ("last_login", "date_joined", "email_verified_at")}),
    )
    add_fieldsets = (
        (None, {"classes": ("wide",), "fields": ("email", "full_name", "password1", "password2")}),
    )


@admin.register(TrustedDevice)
class TrustedDeviceAdmin(admin.ModelAdmin):
    """Support can revoke a remembered device; secrets are never shown (only hashes are stored)."""

    list_display = ("user", "label", "created_at", "last_used_at", "expires_at")
    search_fields = ("user__email",)
    readonly_fields = ("user", "label", "created_at", "last_used_at", "expires_at")
    exclude = ("token_hash", "password_fingerprint")

    def has_add_permission(self, request):
        return False


@admin.register(AuthenticatorDevice, BackupCodeDevice)
class TwoFactorDeviceAdmin(admin.ModelAdmin):
    """Support can remove a user's devices (lost phone and codes). Secrets are never displayed."""

    list_display = ("user", "name", "confirmed", "created_at", "last_used_at")
    search_fields = ("user__email",)
    fields = ("user", "name", "confirmed", "created_at", "last_used_at")
    readonly_fields = fields

    def has_add_permission(self, request):
        return False


# django-otp's own admin pages for its plaintext models could show a secret key / QR code
# or create plaintext devices; this project stores secrets encrypted instead.
for legacy_model in (TOTPDevice, StaticDevice):
    if admin.site.is_registered(legacy_model):
        admin.site.unregister(legacy_model)
