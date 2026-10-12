from django import template

from apps.core.money import format_rupiah

register = template.Library()


@register.filter
def rupiah(value):
    if value is None or value == "":
        return "-"
    return format_rupiah(value)


@register.filter
def rupiah_plain(value):
    if value is None or value == "":
        return "-"
    return format_rupiah(value, with_symbol=False)
