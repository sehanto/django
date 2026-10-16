from django.db.models import QuerySet

from .models import Upload


def uploads_for(organization, *, limit: int = 20) -> QuerySet[Upload]:
    return Upload.objects.for_organization(organization).select_related("created_by")[:limit]
