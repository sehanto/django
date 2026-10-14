"""The choices one level down, for the cascading address selects."""

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404, JsonResponse
from django.views import View

from apps.core.params import choice_param, text_param

from .models import District, Regency, Village
from .services import CODE

CHILDREN = {"regency": (Regency, "province_id"), "district": (District, "regency_id"), "village": (Village, "district_id")}


class RegionChoicesView(LoginRequiredMixin, View):
    def get(self, request):
        level = choice_param(request.GET, "level", list(CHILDREN))
        parent = text_param(request.GET, "parent", max_length=8)
        if not level or not CODE.match(parent):
            raise Http404
        model, parent_field = CHILDREN[level]
        rows = model.objects.filter(is_active=True, **{parent_field: parent}).order_by("name", "code")
        if model is Village:
            choices = [{"code": r.code, "name": r.name, "postal_code": r.postal_code} for r in rows]
        else:
            choices = [{"code": r.code, "name": r.name} for r in rows]
        response = JsonResponse({"choices": choices})
        response["Cache-Control"] = "private, max-age=86400"  # the list changes only with a new Kepmendagri
        return response
