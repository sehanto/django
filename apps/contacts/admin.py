from django.contrib import admin

from .models import Contact


@admin.register(Contact)
class ContactAdmin(admin.ModelAdmin):
    list_display = ("name", "kind", "npwp", "phone", "organization", "is_active")
    list_filter = ("kind", "organization")
    search_fields = ("name", "npwp", "email")
