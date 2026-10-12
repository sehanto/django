from django.contrib import admin

from .models import Membership, Organization, Plan
from .services import delete_organization


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

    def delete_model(self, request, obj):
        delete_organization(organization=obj)

    def delete_queryset(self, request, queryset):
        for organization in queryset:
            delete_organization(organization=organization)


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "monthly_price", "max_users", "max_invoices_per_month", "is_default")
