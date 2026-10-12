from decimal import Decimal

from django import template

register = template.Library()


@register.simple_tag
def bar_height(value, peak) -> str:
    """Percentage height of a bar relative to the chart's peak value."""
    if not peak or value is None or value <= 0:
        return "0"
    return f"{(Decimal(value) / Decimal(peak) * 100):.1f}"
