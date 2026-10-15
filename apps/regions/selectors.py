from django.db.models import QuerySet

from .models import District, Province, Regency, Village


def villages_with_postcode(postcode: str) -> QuerySet[Village]:
    """The active kelurahan/desa with this postcode (one or several share it)."""
    return (
        Village.objects.filter(postal_code=postcode, is_active=True)
        .select_related("district__regency__province").order_by("code")
    )


def village_by_code(code: str) -> Village | None:
    """The active kelurahan/desa with this Kemendagri code (``36.03.22.2008``), or None."""
    return (
        Village.objects.filter(code=(code or "").strip(), is_active=True).select_related("district__regency__province").first()
    )


def active_provinces() -> QuerySet[Province]:
    return Province.objects.filter(is_active=True)


def province_codes() -> list[str]:
    return list(Province.objects.values_list("code", flat=True))


# One level down: the model and the field that points at the level above.
CHILDREN = {"regency": (Regency, "province_id"), "district": (District, "regency_id"), "village": (Village, "district_id")}


def region_children(level: str, parent: str) -> QuerySet:
    """The active regencies, districts or villages under ``parent`` (a code), by name."""
    model, parent_field = CHILDREN[level]
    return model.objects.filter(is_active=True, **{parent_field: parent}).order_by("name", "code")
