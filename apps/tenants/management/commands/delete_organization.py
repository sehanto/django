"""Owner-only offboarding: the web app and its data-only role cannot wipe a tenant's books."""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.core.db_roles import can_purge_ledger, use_owner_connection
from apps.ledger.models import JournalEntry
from apps.tenants.models import Organization
from apps.tenants.services import delete_organization


class Command(BaseCommand):
    help = (
        "Permanently delete an organization and all of its data, including its posted ledger. "
        "Connects as the schema owner (DATABASE_OWNER_URL). Export the books first: "
        "taxpayers must keep records for 10 years (UU KUP Art. 28(11))."
    )

    def add_arguments(self, parser):
        parser.add_argument("organization_id", type=int)
        parser.add_argument("--yes", action="store_true", help="Do not ask for confirmation (for scripts).")

    def handle(self, *args, organization_id, yes, **options):
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

        delete_organization(organization=organization)
        self.stdout.write(self.style.SUCCESS(f"Deleted organization {organization_id} ({organization.name})."))
