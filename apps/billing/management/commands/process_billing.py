"""Daily billing housekeeping. Schedule once a day, e.g. cron: ``15 1 * * * manage.py process_billing``."""

from django.core.management.base import BaseCommand

from apps.billing import services


class Command(BaseCommand):
    help = "Expire stale payment links, send renewal reminders and downgrade lapsed subscriptions."

    def handle(self, *args, **options):
        expired = services.expire_stale_checkouts()
        reminded = services.send_renewal_reminders()
        lapsed = services.lapse_unpaid_subscriptions()
        self.stdout.write(f"Closed {expired} expired payment link(s), sent {reminded} reminder(s), lapsed {lapsed} subscription(s).")
