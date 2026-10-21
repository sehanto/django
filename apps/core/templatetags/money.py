from django import template

from apps.core.formatting import localized_rupiah

register = template.Library()


@register.filter
def rupiah(value):
    if value is None or value == "":
        return "-"
    # A no-break space keeps "Rp" with its figure when a line wraps.
    return localized_rupiah(value).replace("Rp ", "Rp\u00a0", 1)


@register.filter
def rupiah_plain(value):
    if value is None or value == "":
        return "-"
    return localized_rupiah(value, with_symbol=False)
