from django.conf import settings
from django.core.checks import Tags, Warning, register
from django.db import DatabaseError, connection

from .db_roles import connected_role_can_alter_schema


@register(Tags.security, deploy=True)
def check_database_role(app_configs, **kwargs):
    """The app must not connect as a role that could disable the ledger triggers."""
    if connection.vendor != "postgresql":
        return []
    try:
        privileged = connected_role_can_alter_schema()
    except DatabaseError:
        return []  # database unreachable: other checks/commands will say so
    if not privileged:
        return []
    return [
        Warning(
            "The application connects to the database as a superuser or as the owner of the tables, "
            "so it could disable the ledger's immutability triggers.",
            hint="Point DATABASE_URL at a data-only role and DATABASE_OWNER_URL at the owner "
            "(README, 'Database roles'). "
            f"Connected as {settings.DATABASES['default']['USER']!r}.",
            id="core.W001",
        )
    ]
