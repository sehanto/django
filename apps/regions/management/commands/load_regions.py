from pathlib import Path

from django.core.management.base import BaseCommand

from apps.core.db_roles import use_owner_connection
from apps.regions.services import DATA_FILE, load_regions, read_rows


class Command(BaseCommand):
    help = "Load or update the Indonesian regions and postcodes (Kemendagri codes) from apps/regions/data."

    def add_arguments(self, parser):
        parser.add_argument("--file", type=Path, default=DATA_FILE, help="A code,name,postal_code CSV (gzipped).")

    def handle(self, *args, **options):
        use_owner_connection()  # the app role may only read the region tables
        result = load_regions(read_rows(options["file"]))
        self.stdout.write(f"Regions: {result.created} added, {result.updated} updated, {result.deactivated} deactivated.")
