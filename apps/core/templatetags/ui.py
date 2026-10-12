from django import template

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
