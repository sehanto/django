from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError
from django.utils import timezone

from apps.audit.models import AuditEvent
from apps.core.db_roles import connected_role_can_alter_schema, use_owner_connection


class Command(BaseCommand):
    help = "Delete audit events older than AUDIT_RETENTION_DAYS. Runs as the schema owner (DATABASE_OWNER_URL)."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=settings.AUDIT_RETENTION_DAYS)

    def handle(self, *args, days, **options):
        if days < 1:
            raise CommandError("--days must be at least 1.")
        use_owner_connection()
        if not connected_role_can_alter_schema():
            raise CommandError("Only the schema owner may prune the audit log. Set DATABASE_OWNER_URL.")
        cutoff = timezone.now() - timedelta(days=days)
        try:
            deleted, _rows = AuditEvent.objects.filter(created_at__lt=cutoff).delete()
        except IntegrityError as exc:
            raise CommandError("Only the schema owner may prune the audit log. Set DATABASE_OWNER_URL.") from exc
        self.stdout.write(f"Deleted {deleted} audit event(s) older than {days} days.")
