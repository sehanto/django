"""Daily billing housekeeping. Schedule once a day, e.g. cron: ``15 1 * * * manage.py process_billing``."""

from django.core.management.base import BaseCommand

from apps.billing import services


class Command(BaseCommand):
    help = "Charge automatic renewals, expire stale payment links, send renewal reminders and downgrade lapsed subscriptions."

    def handle(self, *args, **options):
        charged, failed = services.charge_auto_renewals()
        self.stdout.write(f"Charged {charged} automatic renewal(s), {failed} failed.")
        expired = services.expire_stale_checkouts()
        reminded = services.send_renewal_reminders()
        lapsed = services.lapse_unpaid_subscriptions()
        self.stdout.write(f"Closed {expired} expired payment link(s), sent {reminded} reminder(s), lapsed {lapsed} subscription(s).")
