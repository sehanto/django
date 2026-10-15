"""The choices one level down, for the cascading address selects, and the kelurahan/desa of a postcode."""

import re

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404, JsonResponse
from django.views import View

from apps.core.params import choice_param, text_param

from .models import Village
from .selectors import CHILDREN, region_children, villages_with_postcode
from .services import CODE

POSTCODE = re.compile(r"^\d{5}$")


class RegionChoicesView(LoginRequiredMixin, View):
    def get(self, request):
        postcode = text_param(request.GET, "postcode", max_length=5)
        if postcode:
            return self.by_postcode(postcode)
        level = choice_param(request.GET, "level", list(CHILDREN))
        parent = text_param(request.GET, "parent", max_length=8)
        if not level or not CODE.match(parent):
            raise Http404
        rows = region_children(level, parent)
        if rows.model is Village:
            choices = [{"code": r.code, "name": r.name, "postal_code": r.postal_code} for r in rows]
        else:
            choices = [{"code": r.code, "name": r.name} for r in rows]
        return _cached(JsonResponse({"choices": choices}))

    def by_postcode(self, postcode: str):
        """Every kelurahan/desa with the postcode, with the levels above it (to fill the selects)."""
        if not POSTCODE.match(postcode):
            raise Http404
        return _cached(JsonResponse({"villages": [
            {"code": v.code, "name": v.name, "district": v.district_id, "regency": v.district.regency_id,
             "province": v.district.regency.province_id, "label": v.region_line}
            for v in villages_with_postcode(postcode)
        ]}))


def _cached(response):
    response["Cache-Control"] = "private, max-age=86400"  # the list changes only with a new Kepmendagri
    return response
