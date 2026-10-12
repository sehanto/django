from django.db.models import QuerySet

from .domain import AuditCategory, actions_in
from .models import AuditEvent


def organization_events(organization, *, category: str = "") -> QuerySet[AuditEvent]:
    events = AuditEvent.objects.filter(organization=organization).select_related("actor")
    if category:
        events = events.filter(action__in=actions_in(AuditCategory(category)))
    return events


def account_events(user) -> QuerySet[AuditEvent]:
    """The user's own sign-ins and security changes (what they can review on the security page)."""
    return AuditEvent.objects.filter(actor=user, action__in=actions_in(AuditCategory.ACCOUNT))
