from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.utils.translation import gettext_lazy as _

from .models import TrustedDevice, User


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
