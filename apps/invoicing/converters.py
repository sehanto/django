from .models import InvoiceKind

SLUG_TO_KIND = {"penjualan": InvoiceKind.SALES, "pembelian": InvoiceKind.PURCHASE}
KIND_TO_SLUG = {kind: slug for slug, kind in SLUG_TO_KIND.items()}


class InvoiceKindConverter:
    """Map Indonesian URL segments to ``InvoiceKind`` values and back."""

    regex = "penjualan|pembelian"

    def to_python(self, value: str) -> str:
        return SLUG_TO_KIND[value]

    def to_url(self, value: str) -> str:
        return KIND_TO_SLUG[value]
