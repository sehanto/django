from django import template
from django.utils import timezone
from django.utils.timesince import timeuntil

from apps.billing.models import SubscriptionInvoice
from apps.contacts.validators import format_npwp
from apps.invoicing.domain import InvoiceStatus
from apps.invoicing.models import InvoiceStatusFilter, Payment
from apps.ledger.models import JournalEntry

register = template.Library()


@register.filter
def add_class(field, css):
    """Render a bound form field with extra CSS classes."""
    existing = field.field.widget.attrs.get("class", "")
    if field.errors:
        css = f"{css} is-invalid"
    return field.as_widget(attrs={"class": f"{existing} {css}".strip()})


# Bootstrap colour per status. Members of different enums that share a value
# (e.g. "paid", "void") are equal as dict keys, so they must share a colour.
STATUS_BADGES = {
    JournalEntry.Status.DRAFT: "secondary",
    JournalEntry.Status.POSTED: "success",
    InvoiceStatus.DRAFT: "secondary",
    InvoiceStatus.ISSUED: "primary",
    InvoiceStatus.PARTIAL: "warning",
    InvoiceStatus.PAID: "success",
    InvoiceStatus.WRITTEN_OFF: "dark",
    InvoiceStatus.VOID: "dark",
    InvoiceStatusFilter.OVERDUE: "danger",
    Payment.Status.POSTED: "success",
    Payment.Status.VOID: "dark",
    SubscriptionInvoice.Status.PENDING: "warning",
    SubscriptionInvoice.Status.PAID: "success",
    SubscriptionInvoice.Status.EXPIRED: "dark",
    SubscriptionInvoice.Status.CANCELED: "dark",
    SubscriptionInvoice.Status.FAILED: "danger",
}


@register.filter
def status_badge(status):
    return STATUS_BADGES.get(status, "secondary")


@register.filter
def timeuntil_delta(delta):
    """Render a timedelta as a localized duration, e.g. "15 minutes" / "15 menit"."""
    reference = timezone.now()
    return timeuntil(reference + delta, reference, depth=1)


@register.filter
def npwp(value) -> str:
    """Display an NPWP: 15 digits dotted, 16 digits in groups of four (see ``format_npwp``)."""
    return format_npwp(str(value)) if value else ""
