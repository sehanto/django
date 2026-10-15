from django.db.models import QuerySet

from .models import Village


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
