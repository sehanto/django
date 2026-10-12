from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from .models import Membership, Organization, Plan


class MembershipInline(admin.TabularInline):
    model = Membership
    extra = 0
    autocomplete_fields = ["user"]


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ("name", "legal_form", "plan", "is_pkp", "created_at")
    list_filter = ("legal_form", "plan", "is_pkp")
    search_fields = ("name", "npwp")
    inlines = [MembershipInline]
    readonly_fields = ["deletion"]

    def has_delete_permission(self, request, obj=None):
        return False  # wiping a tenant's books is owner-only: manage.py delete_organization

    @admin.display(description=_("Deletion"))
    def deletion(self, obj):
        if obj.pk is None:
            return "-"
        return _(
            "Organizations are deleted by the database owner, not in the admin: "
            "manage.py delete_organization %(id)s"
        ) % {"id": obj.pk}


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "monthly_price", "max_users", "max_invoices_per_month", "is_default")
