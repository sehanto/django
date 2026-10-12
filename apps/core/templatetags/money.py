from django import template

from apps.core.formatting import localized_rupiah

register = template.Library()


@register.filter
def rupiah(value):
    if value is None or value == "":
        return "-"
    return localized_rupiah(value)


@register.filter
def rupiah_plain(value):
    if value is None or value == "":
        return "-"
    return localized_rupiah(value, with_symbol=False)
