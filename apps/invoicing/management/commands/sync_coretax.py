"""Bring Coretax submissions up to date and record the faktur numbers issued. Run nightly."""

from django.core.management.base import BaseCommand

from apps.invoicing.coretax_sync import refresh_pending


class Command(BaseCommand):
    help = "Check pending Coretax submissions with the provider and record the faktur numbers issued."

    def handle(self, *args, **options):
        self.stdout.write(f"Settled {refresh_pending()} Coretax submission(s).")
