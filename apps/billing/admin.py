from django.contrib import admin, messages
from django.utils.translation import gettext_lazy as _

from apps.audit import services as audit
from apps.audit.domain import AuditAction
from apps.core.exceptions import DomainError

from . import services
from .models import SubscriptionInvoice


@admin.register(SubscriptionInvoice)
class SubscriptionInvoiceAdmin(admin.ModelAdmin):
    list_display = ("number", "buyer_name", "plan", "billing_period", "total", "status", "paid_at", "coverage_end")
    list_filter = ("status", "billing_period", "plan", "gateway")
    search_fields = ("number", "buyer_name", "gateway_reference")
    readonly_fields = [f.name for f in SubscriptionInvoice._meta.fields]
    actions = ["confirm_bank_transfer", "check_gateway_status"]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False  # sales records are kept

    @admin.action(description=_("Mark as paid (bank transfer confirmed)"))
    def confirm_bank_transfer(self, request, queryset):
        for invoice in queryset:
            try:
                services.mark_paid_manually(invoice=invoice, confirmed_by=request.user)
                audit.record(
                    AuditAction.PAYMENT_CONFIRMED_MANUALLY, request=request, organization=invoice.organization,
                    target=invoice.number, total=invoice.total,
                )
                self.message_user(request, f"{invoice.number}: OK")
            except DomainError as exc:
                self.message_user(request, f"{invoice.number}: {exc.message}", messages.ERROR)

    @admin.action(description=_("Check status with the payment gateway"))
    def check_gateway_status(self, request, queryset):
        for invoice in queryset:
            try:
                invoice = services.sync_payment(invoice)
                self.message_user(request, f"{invoice.number}: {invoice.get_status_display()}")
            except DomainError as exc:
                self.message_user(request, f"{invoice.number}: {exc.message}", messages.ERROR)
