from datetime import timedelta

from axes.handlers.proxy import AxesProxyHandler
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.audit.domain import KEPT_LONGER, AuditCategory, actions_in
from apps.audit.models import AuditEvent
from apps.core.db_roles import connected_role_can_alter_schema, use_owner_connection


class Command(BaseCommand):
    help = (
        "Delete sign-in/account-security audit events and django-axes sign-in records older than "
        "SECURITY_LOG_RETENTION_DAYS, and organization/subscription audit events older than "
        "ORGANIZATION_LOG_RETENTION_DAYS. Runs as the schema owner (DATABASE_OWNER_URL), "
        "the only role allowed to delete audit events."
    )

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=settings.SECURITY_LOG_RETENTION_DAYS,
                            help="Retention for sign-in and account-security records.")
        parser.add_argument("--organization-days", type=int, default=settings.ORGANIZATION_LOG_RETENTION_DAYS,
                            help="Retention for organization and subscription events.")

    def handle(self, *args, days, organization_days, **options):
        if days < 1 or organization_days < 1:
            raise CommandError("Retention periods must be at least 1 day.")
        use_owner_connection()
        if not connected_role_can_alter_schema():
            raise CommandError("Only the schema owner may prune the audit log. Set DATABASE_OWNER_URL.")
        now = timezone.now()
        short_lived = [action for category in AuditCategory if category not in KEPT_LONGER for action in actions_in(category)]
        long_lived = [action for category in KEPT_LONGER for action in actions_in(category)]
        with transaction.atomic():
            security, _rows = AuditEvent.objects.filter(
                action__in=short_lived, created_at__lt=now - timedelta(days=days)
            ).delete()
            organization, _rows = AuditEvent.objects.filter(
                action__in=long_lived, created_at__lt=now - timedelta(days=organization_days)
            ).delete()
            sign_in_records = AxesProxyHandler.reset_logs(age_days=days)
        self.stdout.write(
            f"Deleted {security} security event(s) and {sign_in_records} sign-in record(s) older than {days} days, "
            f"and {organization} organization/subscription event(s) older than {organization_days} days."
        )
