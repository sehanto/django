"""Status tabs over a list page (All · Draft · Unpaid …, each with its count), as in other accounting apps.

A view builds them with ``tabs(request, param, choices, counts)`` and the template renders
``partials/list_toolbar.html``; the chosen value is read the usual way, ``choice_param(params, param, …)``.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from django.db.models import Count, QuerySet
from django.utils.translation import gettext_lazy as _

from .params import choice_param


@dataclass(frozen=True)
class Tab:
    value: str
    label: str
    count: int
    url: str
    current: bool


@dataclass(frozen=True)
class TabSet:
    param: str
    current: str
    tabs: list[Tab]


def counts_by(queryset: QuerySet, field: str) -> dict[str, int]:
    """How many rows have each value of ``field``; ``""`` is the total."""
    counts = {row[field]: row["n"] for row in queryset.values(field).annotate(n=Count("pk")).order_by()}
    counts[""] = sum(counts.values())
    return counts


def tabs(
    request, param: str, choices: Iterable[tuple[str, str]], counts: Mapping[str, int], *,
    optional: Iterable[str] = (), default: str = "",
) -> TabSet:
    """All, then a tab per choice; an ``optional`` value (e.g. void) only shows while it has rows or is chosen.
    With a ``default`` value (a list that opens on one status) there is no All tab.

    Each tab's link keeps the page's other parameters (search, filters) and starts again at page 1."""
    choices = list(choices)
    current = choice_param(request.GET, param, [value for value, _label in choices], default=default)
    optional = set(optional)
    out = []
    for value, label in choices if default else [("", _("All")), *choices]:
        if value in optional and not counts.get(value) and value != current:
            continue
        query = request.GET.copy()
        query.pop("page", None)
        query.pop(param, None)
        if value:
            query[param] = value
        out.append(Tab(value, label, counts.get(value, 0), f"?{query.urlencode()}" if query else "?", value == current))
    return TabSet(param, current, out)
