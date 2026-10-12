from django.contrib import admin

from .models import AuditEvent


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    """Read-only: the database rejects changes and deletes anyway (migration 0002)."""

    list_display = ("created_at", "action", "actor_email", "organization_name", "target", "ip_address")
    list_filter = ("action",)
    search_fields = ("actor_email", "organization_name", "target", "ip_address")
    date_hierarchy = "created_at"
    readonly_fields = [field.name for field in AuditEvent._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
