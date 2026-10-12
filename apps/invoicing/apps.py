from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class InvoicingConfig(AppConfig):
    name = "apps.invoicing"
    verbose_name = _("Sales & Purchases")

    def ready(self):
        from django.urls import register_converter

        from .converters import InvoiceKindConverter

        register_converter(InvoiceKindConverter, "invoice_kind")
