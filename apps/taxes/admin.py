from django.contrib import admin

from .models import PphFinalPayment, TaxRate


@admin.register(TaxRate)
class TaxRateAdmin(admin.ModelAdmin):
    list_display = ("name", "rate", "dpp_numerator", "dpp_denominator", "organization", "is_active")
    list_filter = ("organization",)


@admin.register(PphFinalPayment)
class PphFinalPaymentAdmin(admin.ModelAdmin):
    list_display = ("organization", "year", "month", "tax_amount", "paid_on", "ntpn")
    list_filter = ("organization", "year")
