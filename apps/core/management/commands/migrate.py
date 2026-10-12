from django.core.management.commands.migrate import Command as MigrateCommand

from apps.core.db_roles import use_owner_connection


class Command(MigrateCommand):
    """``migrate`` as the schema owner (``DATABASE_OWNER_URL``); the app role then gets its grants."""

    def handle(self, *args, **options):
        use_owner_connection(options["database"])
        return super().handle(*args, **options)
