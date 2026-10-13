"""Post the depreciation that is due for every organization. Schedule daily, e.g. cron:
``0 2 * * * manage.py post_depreciation`` (deploy/cron/crontab). Safe to run repeatedly:
each month is posted once, on its last day, and missed months are caught up."""

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.assets import services
from apps.assets.models import FixedAsset
from apps.core.exceptions import DomainError
from apps.tenants.models import Organization


class Command(BaseCommand):
    help = "Post monthly depreciation of the fixed asset register for every organization."

    def handle(self, *args, **options):
        today = timezone.localdate()
        with_assets = FixedAsset.objects.filter(status=FixedAsset.Status.ACTIVE, is_depreciable=True).values("organization_id")
        posted = skipped = 0
        for organization in Organization.objects.filter(pk__in=with_assets).order_by("pk"):
            try:
                posted += len(services.post_depreciation(organization=organization, through=today))
            except DomainError as exc:
                # E.g. a locked period: the organization has to act; the register shows it.
                skipped += 1
                self.stderr.write(f"Organization {organization.pk}: depreciation not posted: {exc}")
        self.stdout.write(f"Posted {posted} depreciation entr{'y' if posted == 1 else 'ies'}; {skipped} organization(s) skipped.")
