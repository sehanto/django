"""Breadcrumbs above a page's title, from the sidebar section down to the page itself:

    {% block breadcrumbs %}{% breadcrumbs %}
      {% crumb _("Payroll") "payroll:index" year=run.year %}
      {% crumb run_title %}
    {% endbreadcrumbs %}{% endblock %}

``crumb`` takes the label, then a URL name with its arguments (keyword arguments become the query
string); the last crumb has no URL: it is the current page (``aria-current="page"``). Every trail
starts at the dashboard, which the tag adds; the dashboard's own page has an empty
``{% breadcrumbs %}{% endbreadcrumbs %}`` and shows just that. Built-in like ``{% icon %}``; the
separators come from the ``breadcrumbs`` component in ``frontend/app.css``.
"""

from urllib.parse import urlencode

from django import template
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.translation import gettext as _

register = template.Library()


class BreadcrumbsNode(template.Node):
    def __init__(self, nodelist):
        self.nodelist = nodelist

    def render(self, context):
        items = self.nodelist.render(context)
        home = crumb(_("Dashboard"), "dashboard:home" if items.strip() else "")  # the start of every trail
        return render_to_string("partials/breadcrumbs.html", {"items": home + items})


@register.tag
def breadcrumbs(parser, token):
    nodelist = parser.parse(("endbreadcrumbs",))
    parser.delete_first_token()
    return BreadcrumbsNode(nodelist)


@register.simple_tag
def crumb(label, url_name="", *args, **query):
    href = ""
    if url_name:
        href = reverse(url_name, args=args)
        if query := {key: value for key, value in query.items() if value not in (None, "")}:
            href = f"{href}?{urlencode(query)}"
    return render_to_string("partials/crumb.html", {"label": label, "href": href})
