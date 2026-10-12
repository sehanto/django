from .models import InvoiceKind


def invoice_kinds(request):
    """Let shared templates (the sidebar) refer to ``InvoiceKind`` members instead of raw values."""
    return {"invoice_kinds": InvoiceKind}
