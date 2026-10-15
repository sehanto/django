from dataclasses import dataclass

from django.apps import apps
from django.db import connection
from django.db.models import QuerySet
from django.utils.text import capfirst
from django.utils.translation import gettext

from .domain import AuditCategory, actions_in
from .models import AuditEvent, RecordChange


def organization_events(organization, *, category: str = "") -> QuerySet[AuditEvent]:
    events = AuditEvent.objects.filter(organization=organization).select_related("actor")
    if category:
        events = events.filter(action__in=actions_in(AuditCategory(category)))
    return events


def account_events(user) -> QuerySet[AuditEvent]:
    """The user's own sign-ins and security changes (what they can review on the security page)."""
    return AuditEvent.objects.filter(actor=user, action__in=actions_in(AuditCategory.ACCOUNT))


# --- The audit trail (record changes) ------------------------------------------------------

MAX_VALUE_LENGTH = 120


@dataclass(frozen=True)
class FieldChange:
    label: str
    old: str
    new: str


@dataclass(frozen=True)
class TrailProblem:
    sequence: int
    problem: str


def tracked_tables() -> dict[str, str]:
    """{table: its name for people} for every table the trail records, by model."""
    names = {m._meta.db_table: capfirst(str(m._meta.verbose_name_plural)) for m in apps.get_models()}
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT DISTINCT c.relname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid WHERE t.tgname = 'audit_trail'"
        )
        tables = [row[0] for row in cursor.fetchall()]
    return dict(sorted(((t, names.get(t, t)) for t in tables), key=lambda item: item[1]))


def record_changes(organization, *, table: str = "", record: str = "") -> QuerySet[RecordChange]:
    changes = RecordChange.objects.filter(organization=organization)
    if table:
        changes = changes.filter(table_name=table)
        if record:
            changes = changes.filter(record_id=record)
    return changes


def _labels(table: str) -> dict[str, str]:
    for model in apps.get_models():
        if model._meta.db_table == table:
            return {f.column: capfirst(str(f.verbose_name)) for f in model._meta.concrete_fields}
    return {}


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return gettext("Yes") if value else gettext("No")
    text = value if isinstance(value, str) else str(value)
    return text if len(text) <= MAX_VALUE_LENGTH else text[:MAX_VALUE_LENGTH] + "…"


def field_changes(change: RecordChange) -> list[FieldChange]:
    """What changed, column by column, in the model's own words."""
    labels = _labels(change.table_name)
    if change.operation == RecordChange.Operation.UPDATE:
        rows = [(column, old, new) for column, (old, new) in change.changes.items()]
    elif change.operation == RecordChange.Operation.INSERT:
        rows = [(column, None, value) for column, value in change.changes.items() if value not in (None, "", [], {})]
    else:
        rows = [(column, value, None) for column, value in change.changes.items() if value not in (None, "", [], {})]
    return [FieldChange(labels.get(column, column), _text(old), _text(new)) for column, old, new in sorted(rows)]


def verify_trail(organization) -> list[TrailProblem]:
    with connection.cursor() as cursor:
        cursor.execute("SELECT out_sequence, out_problem FROM audit_verify_chain(%s)", [organization.pk])
        return [TrailProblem(*row) for row in cursor.fetchall()]


def trail_head(organization) -> tuple[int, str] | None:
    last = RecordChange.objects.filter(organization=organization).order_by("-sequence").values_list("sequence", "change_hash").first()
    return tuple(last) if last else None


def trail_mismatch(organization, sequence: int, change_hash: str) -> str | None:
    """Compare with a head noted earlier: changes removed from the end, or a rewritten trail."""
    kept = RecordChange.objects.filter(organization=organization, sequence=sequence).values_list("change_hash", flat=True).first()
    if kept is None:
        return f"change #{sequence} of the recorded head is missing"
    if kept != change_hash:
        return f"change #{sequence} is no longer {change_hash} (the trail was rewritten)"
    return None
