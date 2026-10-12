from unittest import mock

from django.core.management import get_commands
from django.test import override_settings

from apps.core import db_roles
from apps.core.checks import check_database_role

OWNER = {"NAME": "msme_db", "USER": "msme_user", "PASSWORD": "owner-secret", "HOST": "db", "PORT": "5432"}


class FakeCursor:
    def __init__(self, current_user):
        self.current_user = current_user
        self.statements = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.statements.append(sql)

    def fetchone(self):
        return (self.current_user, "public")


class FakeConnection:
    vendor = "postgresql"

    def __init__(self, current_user="msme_user"):
        self.settings_dict = {"NAME": "msme_db", "USER": "msme_app", "PASSWORD": "app-secret", "HOST": "db", "PORT": "5432"}
        self.closed = False
        self.ops = mock.Mock(quote_name=lambda name: f'"{name}"')
        self._cursor = FakeCursor(current_user)

    def close(self):
        self.closed = True

    def cursor(self):
        return self._cursor


def test_schema_commands_use_the_owner_aware_versions():
    commands = get_commands()
    assert commands["migrate"] == "apps.core"
    assert commands["createcachetable"] == "apps.core"


@override_settings(DATABASE_OWNER=OWNER)
def test_owner_connection_swaps_credentials_only():
    connection = FakeConnection()
    with mock.patch.object(db_roles, "connections", {"default": connection}):
        db_roles.use_owner_connection()
    assert connection.closed
    assert connection.settings_dict["USER"] == "msme_user"
    assert connection.settings_dict["PASSWORD"] == "owner-secret"
    assert connection.settings_dict["NAME"] == "msme_db"


@override_settings(DATABASE_OWNER=None)
def test_without_owner_url_the_connection_is_untouched():
    connection = FakeConnection()
    with mock.patch.object(db_roles, "connections", {"default": connection}):
        db_roles.use_owner_connection()
    assert not connection.closed and connection.settings_dict["USER"] == "msme_app"


@override_settings(DATABASE_APP_ROLE="msme_app")
def test_owner_grants_row_access_but_not_migration_history():
    connection = FakeConnection(current_user="msme_user")
    with mock.patch.object(db_roles, "connections", {"default": connection}):
        assert db_roles.grant_app_privileges() is True
    statements = connection._cursor.statements[1:]
    assert 'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA "public" TO "msme_app"' in statements
    assert 'REVOKE INSERT, UPDATE, DELETE ON "public".django_migrations FROM "msme_app"' in statements
    assert not any(word in s for s in statements for word in ("TRUNCATE", "TRIGGER", "CREATE ON", "ALL PRIVILEGES"))


@override_settings(DATABASE_APP_ROLE="msme_app")
def test_no_grants_when_not_connected_as_owner():
    connection = FakeConnection(current_user="msme_app")
    with mock.patch.object(db_roles, "connections", {"default": connection}):
        assert db_roles.grant_app_privileges() is False
    assert len(connection._cursor.statements) == 1  # only the "who am I" query


@override_settings(DATABASE_APP_ROLE=None)
def test_single_role_setups_skip_grants():
    assert db_roles.grant_app_privileges() is False


def test_deploy_check_flags_an_owner_connection(db):
    # The test database is created and owned by the connected role.
    assert [warning.id for warning in check_database_role(None)] == ["core.W001"]
    with mock.patch("apps.core.checks.connected_role_can_alter_schema", return_value=False):
        assert check_database_role(None) == []
