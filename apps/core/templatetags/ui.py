from django import template
from django.utils import timezone
from django.utils.timesince import timeuntil

register = template.Library()


@register.filter
def add_class(field, css):
    """Render a bound form field with extra CSS classes."""
    existing = field.field.widget.attrs.get("class", "")
    if field.errors:
        css = f"{css} is-invalid"
    return field.as_widget(attrs={"class": f"{existing} {css}".strip()})


STATUS_BADGES = {
    "draft": "secondary",
    "posted": "success",
    "issued": "primary",
    "partial": "warning",
    "paid": "success",
    "void": "dark",
    "overdue": "danger",
    "pending": "warning",
    "expired": "dark",
    "canceled": "dark",
    "failed": "danger",
}


@register.filter
def status_badge(status):
    return STATUS_BADGES.get(status, "secondary")


@register.filter
def timeuntil_delta(delta):
    """Render a timedelta as a localized duration, e.g. "15 minutes" / "15 menit"."""
    reference = timezone.now()
    return timeuntil(reference + delta, reference, depth=1)
