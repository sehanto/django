from django.db.models import QuerySet

from .models import Village


def villages_with_postcode(postcode: str) -> QuerySet[Village]:
    """The active kelurahan/desa with this postcode (one or several share it)."""
    return (
        Village.objects.filter(postal_code=postcode, is_active=True)
        .select_related("district__regency__province").order_by("code")
    )
