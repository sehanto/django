from django import template
from django.utils.translation import gettext as _
from django.utils.translation import ngettext

from apps.audit.domain import AuditAction
from apps.ledger.models import Account
from apps.tenants.models import Organization
from apps.tenants.roles import Role

register = template.Library()


def _show(value) -> str:
    if value is None or value == "":
        return "-"
    if isinstance(value, bool):
        return _("Yes") if value else _("No")
    return str(value)


def _field_label(model, name: str) -> str:
    try:
        return str(model._meta.get_field(name).verbose_name)
    except Exception:  # a field that no longer exists
        return name


CHANGED_MODEL = {AuditAction.ORGANIZATION_SETTINGS_CHANGED: Organization, AuditAction.ACCOUNT_CHANGED: Account}


def _role(value) -> str:
    return str(Role(value).label) if value in Role.values else _show(value)


@register.filter
def audit_summary(event) -> str:
    """One readable line for an event's target and details (never secrets: none are recorded)."""
    details, action = event.details or {}, event.action
    if action in CHANGED_MODEL:
        changes = details.get("changes", {})
        summary = "; ".join(
            f"{_field_label(CHANGED_MODEL[action], name)}: {_show(old)} → {_show(new)}" for name, (old, new) in changes.items()
        )
        return f"{event.target}: {summary}" if event.target else summary
    if action in (AuditAction.MEMBER_ADDED, AuditAction.MEMBER_REMOVED):
        return f"{event.target} ({_role(details.get('role'))})"
    if action == AuditAction.LOGIN_FAILED and details.get("step") == "two_factor":
        return _("%(email)s, wrong two-factor code") % {"email": event.target}
    if action == AuditAction.PLAN_CHANGED:
        return f"{_show(details.get('previous_plan'))} → {event.target}"
    if action == AuditAction.EMAIL_CHANGED:
        return f"{_show(details.get('previous'))} → {event.target}"
    if action == AuditAction.DEVICES_FORGOTTEN:
        count = details.get("count", 0)
        return ngettext("%(count)s device", "%(count)s devices", count) % {"count": count}
    if action == AuditAction.BOOKS_EXPORTED and details.get("via") == "command":
        return _("By the system operator")
    return event.target
