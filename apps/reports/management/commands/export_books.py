from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import translation

from apps.audit import services as audit
from apps.audit.domain import AuditAction
from apps.reports.exports import write_books_archive
from apps.tenants.models import Organization


class Command(BaseCommand):
    help = "Write an organization's complete books (ZIP of CSV files) to a file."

    def add_arguments(self, parser):
        parser.add_argument("organization_id", type=int)
        parser.add_argument("path", type=Path, help="Where to write the ZIP file.")
        parser.add_argument(
            "--language", default=settings.LANGUAGE_CODE, choices=[code for code, _name in settings.LANGUAGES],
            help="Language of the column headers and README.",
        )

    def handle(self, *args, organization_id, path, language, **options):
        organization = Organization.objects.filter(pk=organization_id).first()
        if organization is None:
            raise CommandError(f"Organization {organization_id} does not exist.")
        export_books(organization, path, language)
        self.stdout.write(self.style.SUCCESS(f"Wrote the books of {organization.name} to {path}."))


def export_books(organization: Organization, path: Path, language: str) -> None:
    if path.exists():
        raise CommandError(f"{path} already exists; choose a new file name.")
    try:
        with translation.override(language), path.open("xb") as target:
            write_books_archive(organization, target)
        audit.record(AuditAction.BOOKS_EXPORTED, organization=organization, target=path.name, via="command")
    except BaseException:
        path.unlink(missing_ok=True)  # never leave a truncated archive behind
        raise
