"""Two database roles: the owner changes the schema, the app role only reads and writes rows.

The app role cannot ALTER tables or DISABLE TRIGGER, so the ledger's
immutability triggers hold even if the application is compromised.
"""

from django.conf import settings
from django.db import connections

OWNER_KEYS = ("USER", "PASSWORD", "HOST", "PORT")

APP_ROLE_GRANTS = (
    "GRANT USAGE ON SCHEMA {schema} TO {role}",
    "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {schema} TO {role}",
    "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA {schema} TO {role}",
    # Tables created later by the owner outside migrate (e.g. createcachetable).
    "ALTER DEFAULT PRIVILEGES IN SCHEMA {schema} GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {role}",
    "ALTER DEFAULT PRIVILEGES IN SCHEMA {schema} GRANT USAGE, SELECT ON SEQUENCES TO {role}",
    # Wiping a tenant's ledger is owner-only (manage.py delete_organization), never the app.
    "REVOKE EXECUTE ON FUNCTION {schema}.ledger_purge_organization(bigint) FROM {role}",
    # Migration history is the owner's business.
    "REVOKE INSERT, UPDATE, DELETE ON {schema}.django_migrations FROM {role}",
)


def use_owner_connection(using: str = "default") -> None:
    """Reconnect ``using`` as the schema owner when ``DATABASE_OWNER_URL`` is set."""
    owner = settings.DATABASE_OWNER
    if not owner:
        return
    connection = connections[using]
    connection.close()
    connection.settings_dict.update({key: owner[key] for key in OWNER_KEYS})


def grant_app_privileges(using: str = "default") -> bool:
    """Give the app role row access to every table (idempotent). Runs after each migrate."""
    role = settings.DATABASE_APP_ROLE
    connection = connections[using]
    if not role or connection.vendor != "postgresql":
        return False
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_user, current_schema()")
        current_user, schema = cursor.fetchone()
        if current_user == role:
            return False  # not connected as the owner; nothing we may grant
        names = {"role": connection.ops.quote_name(role), "schema": connection.ops.quote_name(schema)}
        for statement in APP_ROLE_GRANTS:
            cursor.execute(statement.format(**names))
    return True


def connected_role_can_alter_schema(using: str = "default") -> bool:
    """True when the connected role is a superuser or owns (or is a member of the owner of) the tables."""
    connection = connections[using]
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT rolsuper OR EXISTS (
                SELECT 1 FROM pg_tables
                 WHERE schemaname = current_schema() AND pg_has_role(current_user, tableowner, 'USAGE')
            )
            FROM pg_roles WHERE rolname = current_user
            """
        )
        return bool(cursor.fetchone()[0])


def can_purge_ledger(using: str = "default") -> bool:
    """Whether the connected role may wipe a tenant's ledger (the schema owner; never the app role)."""
    with connections[using].cursor() as cursor:
        cursor.execute("SELECT has_function_privilege('ledger_purge_organization(bigint)', 'EXECUTE')")
        return bool(cursor.fetchone()[0])
