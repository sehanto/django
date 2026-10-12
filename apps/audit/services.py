from apps.accounts.client_ip import client_ip
from apps.core.sanitizers import sanitize_text

from .domain import AuditAction
from .models import AuditEvent

USER_AGENT_LENGTH = AuditEvent._meta.get_field("user_agent").max_length
TARGET_LENGTH = AuditEvent._meta.get_field("target").max_length


def record(action: AuditAction, *, request=None, actor=None, organization=None, target="", **details) -> AuditEvent:
    """Append one event. ``request`` supplies the IP address, browser and (if signed in) the actor.

    Call it after the action succeeded, inside the same transaction when there is one.
    Never pass secrets (passwords, codes, keys) in ``details``.
    """
    if actor is None and request is not None and getattr(request, "user", None) is not None and request.user.is_authenticated:
        actor = request.user
    return AuditEvent.objects.create(
        action=action,
        actor=actor,
        actor_email=getattr(actor, "email", "") or "",
        organization=organization,
        organization_name=organization.name if organization else "",
        ip_address=client_ip(request) if request is not None else None,
        user_agent=sanitize_text(request.META.get("HTTP_USER_AGENT", ""))[:USER_AGENT_LENGTH] if request is not None else "",
        target=sanitize_text(str(target))[:TARGET_LENGTH],
        details=details,
    )
