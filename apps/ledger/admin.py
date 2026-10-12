from django.contrib import admin

from .models import Account, JournalEntry, JournalLine


@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "category", "system_role", "organization", "is_active")
    list_filter = ("category", "organization")
    search_fields = ("code", "name")


class JournalLineInline(admin.TabularInline):
    model = JournalLine
    extra = 0
    readonly_fields = ("account", "description", "debit", "credit")
    can_delete = False


@admin.register(JournalEntry)
class JournalEntryAdmin(admin.ModelAdmin):
    """Read-only: ledger mutations must go through the service layer."""

    list_display = ("number", "date", "description", "status", "source", "organization")
    list_filter = ("status", "source", "organization")
    search_fields = ("number", "description", "reference")
    inlines = [JournalLineInline]

    def has_change_permission(self, request, obj=None):
        return False

    def has_add_permission(self, request):
        return False
