from django.apps import AppConfig


class InvoicingConfig(AppConfig):
    name = "apps.invoicing"
    verbose_name = "Penjualan & Pembelian"

    def ready(self):
        from django.urls import register_converter

        from .converters import InvoiceKindConverter

        register_converter(InvoiceKindConverter, "invoice_kind")
