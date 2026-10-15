"""Recognise the prepayments due for every organization. Schedule daily, e.g. cron:
``5 2 * * * manage.py post_deferrals`` (deploy/cron/crontab). Safe to run repeatedly:
each month is recognised once, on its last day, and missed months are caught up."""

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.core.exceptions import DomainError
from apps.deferrals import services
from apps.deferrals.models import Deferral
from apps.tenants.models import Organization


class Command(BaseCommand):
    help = "Post the monthly recognition of amounts paid or received in advance for every organization."

    def handle(self, *args, **options):
        today = timezone.localdate()
        with_deferrals = Deferral.objects.filter(status=Deferral.Status.ACTIVE).values("organization_id")
        posted = skipped = 0
        for organization in Organization.objects.filter(pk__in=with_deferrals).order_by("pk"):
            try:
                posted += len(services.post_deferrals(organization=organization, through=today))
            except DomainError as exc:
                # E.g. a locked period: the organization has to act; the register shows it.
                skipped += 1
                self.stderr.write(f"Organization {organization.pk}: prepayments not recognised: {exc}")
        self.stdout.write(f"Posted {posted} entr{'y' if posted == 1 else 'ies'}; {skipped} organization(s) skipped.")
