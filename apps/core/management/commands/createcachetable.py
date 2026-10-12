from django.core.management.commands.createcachetable import Command as CreateCacheTableCommand

from apps.core.db_roles import use_owner_connection


class Command(CreateCacheTableCommand):
    """``createcachetable`` as the schema owner (``DATABASE_OWNER_URL``)."""

    def handle(self, *args, **options):
        use_owner_connection(options["database"])
        return super().handle(*args, **options)
