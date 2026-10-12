"""Owner-only offboarding: the web app and its data-only role cannot wipe a tenant's books."""

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.audit import services as audit
from apps.audit.domain import AuditAction
from apps.core.db_roles import can_purge_ledger, use_owner_connection
from apps.ledger.models import JournalEntry
from apps.reports.management.commands.export_books import export_books
from apps.tenants.models import Organization
from apps.tenants.services import delete_organization


class Command(BaseCommand):
    help = (
        "Permanently delete an organization and all of its data, including its posted ledger. "
        "Connects as the schema owner (DATABASE_OWNER_URL). Requires --export-to (books ZIP written first) or "
        "an explicit --skip-export: taxpayers must keep records for 10 years (UU KUP Art. 28(11))."
    )

    def add_arguments(self, parser):
        parser.add_argument("organization_id", type=int)
        parser.add_argument("--yes", action="store_true", help="Do not ask for confirmation (for scripts).")
        export = parser.add_mutually_exclusive_group(required=True)
        export.add_argument("--export-to", type=Path, help="Write the books (ZIP of CSV files) here before deleting.")
        export.add_argument("--skip-export", action="store_true", help="Delete without exporting the books.")

    def handle(self, *args, organization_id, yes, export_to, skip_export, **options):
        use_owner_connection()
        organization = Organization.objects.filter(pk=organization_id).first()
        if organization is None:
            raise CommandError(f"Organization {organization_id} does not exist.")
        if not can_purge_ledger():
            raise CommandError(
                f"Connected as {settings.DATABASES['default']['USER']!r}, which may not delete ledgers. "
                "Set DATABASE_OWNER_URL to the schema owner."
            )

        entries = JournalEntry.objects.filter(organization=organization).count()
        members = organization.memberships.count()
        self.stdout.write(
            f"Organization {organization.pk}: {organization.name!r}, {members} member(s), {entries} journal entr(y/ies). "
            "This cannot be undone."
        )
        if not yes:
            answer = input(f"Type the organization name ({organization.name}) to delete it: ")
            if answer.strip() != organization.name:
                raise CommandError("Name did not match; nothing was deleted.")

        if export_to:
            export_books(organization, export_to, settings.LANGUAGE_CODE)
            self.stdout.write(f"Books exported to {export_to}.")
        with transaction.atomic():
            audit.record(  # kept after the deletion: the organization's name is snapshotted
                AuditAction.ORGANIZATION_DELETED, organization=organization, target=organization.name,
                organization_id=organization.pk, journal_entries=entries, exported_to=str(export_to) if export_to else None,
            )
            delete_organization(organization=organization)
        self.stdout.write(self.style.SUCCESS(f"Deleted organization {organization_id} ({organization.name})."))
