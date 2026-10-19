"""``{% icon "receipt" "me-2 text-primary" %}``: a Lucide icon (ISC) as inline SVG.

The SVGs are templates in ``templates/icons/``, copied by ``manage.py build_css`` from the pinned
``lucide-static`` package for the icons the templates use (``apps.core.assets``). They are
decorative (``aria-hidden``): give icon-only buttons an ``aria-label``. The icon is 1em square
and follows the text colour, so text utilities (``text-xl``, ``text-primary``) size and colour it.
"""

import re

from django import template
from django.template.loader import render_to_string

register = template.Library()
NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


@register.simple_tag
def icon(name: str, css: str = "") -> str:
    if not NAME.match(name):
        raise template.TemplateSyntaxError(f"Not an icon name: {name!r}")
    return render_to_string(f"icons/{name}.svg", {"css": css})
