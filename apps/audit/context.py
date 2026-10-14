"""Who is changing the books. The database records it with every change in the audit trail
(audit 0010 reads ``audit.actor_id``/``audit.actor``); the application only names the actor:
the signed-in user of a request, the API key's creator, or the management command."""

import sys
import threading

from django.db import DatabaseError, connections
from django.db.backends.signals import connection_created

_state = threading.local()
NOT_COMMANDS = {"runserver"}


def default_label() -> str:
    """``command:<name>`` for management commands (cron); empty for web processes."""
    argv = sys.argv
    if len(argv) > 1 and argv[0].endswith("manage.py") and argv[1] not in NOT_COMMANDS:
        return f"command:{argv[1]}"
    return ""


def _apply(connection, actor_id: str, label: str) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT set_config('audit.actor_id', %s, false), set_config('audit.actor', %s, false)", [actor_id, label[:254]]
        )


def set_actor(user=None, label: str = "", using: str = "default") -> None:
    """Name who makes the following changes on this connection, until ``reset_actor``."""
    connection = connections[using]
    if connection.vendor != "postgresql":
        return
    _apply(connection, str(user.pk) if user is not None else "", label or (user.email if user is not None else ""))
    _state.dirty = True


def reset_actor(using: str = "default") -> None:
    """Back to the process default; a connection that can't be reset is closed, so no request
    inherits another's actor."""
    if not getattr(_state, "dirty", False):
        return
    _state.dirty = False
    connection = connections[using]
    try:
        _apply(connection, "", default_label())
    except DatabaseError:
        connection.close()


def _on_connect(sender, connection, **kwargs):
    if connection.vendor == "postgresql" and (label := default_label()):
        _apply(connection, "", label)


def connect() -> None:
    connection_created.connect(_on_connect, dispatch_uid="audit-actor")


class AuditActorMiddleware:
    """Changes made while handling a signed-in user's request are recorded as theirs."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            set_actor(user)
        try:
            return self.get_response(request)
        finally:
            reset_actor()
