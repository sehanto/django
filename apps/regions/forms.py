"""Cascading Province → Kabupaten/Kota → Kecamatan → Kelurahan/Desa selects for a model with
``regions.models.Address``. Only the village is stored; the levels above it narrow the choices
(static/js/regions.js reloads them) and are checked against it. A submission with only the
village (the API) is enough: the levels above follow from it."""

from django import forms
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from .models import District, Province, Regency, Village
from .services import CODE

LEVELS = ("province", "regency", "district", "village")


class VillageSelect(forms.Select):
    """Each option carries its postcode, which the page shows next to the selects."""

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex=subindex, attrs=attrs)
        instance = getattr(value, "instance", None)
        if instance is not None:
            option["attrs"]["data-postal-code"] = instance.postal_code
        return option


def _code(value) -> str | None:
    """A submitted region code, or None (never pass a malformed value to the ORM)."""
    return value if isinstance(value, str) and CODE.match(value) else None


def _select(level: str) -> dict:
    return {"data-region-level": level, "data-region-url": reverse("regions:choices")}


class RegionFieldsMixin:
    """Adds the region selects after the ``address`` field. ``address_required(cleaned_data)``
    says whether this address must name its kelurahan/desa."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        village = self.instance.village if getattr(self.instance, "village_id", None) else None
        district = village.district if village else None
        regency = district.regency if district else None
        current = {"province": regency.province_id if regency else None, "regency": regency.pk if regency else None,
                   "district": district.pk if district else None, "village": village.pk if village else None}
        chosen = {level: _code(self.data.get(self.add_prefix(level))) if self.is_bound else current[level] for level in LEVELS}
        active = {"is_active": True}
        querysets = {
            "province": Province.objects.filter(**active),
            "regency": Regency.objects.filter(province_id=chosen["province"], **active),
            "district": District.objects.filter(regency_id=chosen["regency"], **active),
            "village": Village.objects.filter(district_id=chosen["district"], **active),
        }
        if self.is_bound and chosen["village"] and not chosen["district"]:  # only the village given
            querysets["village"] = Village.objects.filter(pk=chosen["village"], **active)
        labels = {"province": _("Province"), "regency": _("Regency/city"), "district": _("District (kecamatan)"),
                  "village": _("Kelurahan/desa")}
        fields = {}
        for level in LEVELS:
            fields[level] = forms.ModelChoiceField(
                label=labels[level], queryset=querysets[level], required=False, initial=current[level],
                empty_label="—", widget=(VillageSelect if level == "village" else forms.Select)(attrs=_select(level)),
            )
        fields["postal_code"] = forms.CharField(
            label=_("Postcode"), required=False, disabled=True, initial=village.postal_code if village else "",
            widget=forms.TextInput(attrs={"data-region-postal-code": "", "readonly": True}),
            help_text=_("Follows from the kelurahan/desa."),
        )
        ordered = {}
        for name, field in self.fields.items():
            if name in LEVELS:
                continue
            ordered[name] = field
            if name == "address":
                ordered.update(fields)
        self.fields = ordered

    def address_required(self, cleaned_data) -> bool:
        """By default: whenever any part of the address is given."""
        return bool(cleaned_data.get("address") or any(cleaned_data.get(level) for level in LEVELS))

    def clean(self):
        cleaned = super().clean()
        village = cleaned.get("village")
        if village is not None:
            district = village.district
            parents = {"district": district, "regency": district.regency, "province": district.regency.province}
            for level, parent in parents.items():
                given = cleaned.get(level)
                if given is not None and given != parent:
                    self.add_error(level, _("This doesn't match the kelurahan/desa chosen."))
        elif self.address_required(cleaned) and not self.errors.get("village"):
            self.add_error("village", _("Choose the province, regency/city, district and kelurahan/desa."))
        for helper in (*LEVELS[:-1], "postal_code"):  # only the village is stored
            cleaned.pop(helper, None)
        return cleaned
