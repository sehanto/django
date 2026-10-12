from django.contrib import admin

from .models import Invoice, InvoiceLine, Item, Payment


class InvoiceLineInline(admin.TabularInline):
    model = InvoiceLine
    extra = 0


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ("display_number", "kind", "contact", "date", "total", "status", "organization")
    list_filter = ("kind", "status", "organization")
    search_fields = ("number", "contact__name")
    inlines = [InvoiceLineInline]
    readonly_fields = ("journal_entry", "subtotal", "tax_amount", "total", "amount_paid")


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("number", "invoice", "date", "amount", "status", "organization")
    list_filter = ("status", "organization")


@admin.register(Item)
class ItemAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "type", "sale_price", "organization")
    list_filter = ("type", "organization")
