from datetime import timedelta

from axes.handlers.proxy import AxesProxyHandler
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.audit.models import AuditEvent
from apps.core.db_roles import connected_role_can_alter_schema, use_owner_connection


class Command(BaseCommand):
    help = (
        "Delete audit events and django-axes sign-in records (IP address, browser) older than "
        "SECURITY_LOG_RETENTION_DAYS, so both follow one retention period. "
        "Runs as the schema owner (DATABASE_OWNER_URL), the only role allowed to delete audit events."
    )

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=settings.SECURITY_LOG_RETENTION_DAYS)

    def handle(self, *args, days, **options):
        if days < 1:
            raise CommandError("--days must be at least 1.")
        use_owner_connection()
        if not connected_role_can_alter_schema():
            raise CommandError("Only the schema owner may prune the audit log. Set DATABASE_OWNER_URL.")
        cutoff = timezone.now() - timedelta(days=days)
        with transaction.atomic():
            audit_events, _rows = AuditEvent.objects.filter(created_at__lt=cutoff).delete()
            sign_in_records = AxesProxyHandler.reset_logs(age_days=days)
        self.stdout.write(
            f"Deleted {audit_events} audit event(s) and {sign_in_records} sign-in record(s) older than {days} days."
        )
