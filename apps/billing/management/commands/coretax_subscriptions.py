"""The platform's Faktur Pajak for a month's paid subscription invoices: write the Coretax
import file, or send it through the platform's Coretax connection (``CORETAX_CONNECTOR``).

    manage.py coretax_subscriptions 2026 10 --output faktur-langganan-2026-10.xml
    manage.py coretax_subscriptions 2026 10 --send
"""

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.billing import coretax
from apps.invoicing.coretax_api import DocumentType
from apps.invoicing.coretax_sync import apply_result, platform_connector
from apps.invoicing.models import CoretaxSubmission


class Command(BaseCommand):
    help = "Export or send the Faktur Pajak of a month's paid subscription invoices."

    def add_arguments(self, parser):
        parser.add_argument("year", type=int)
        parser.add_argument("month", type=int, choices=range(1, 13))
        target = parser.add_mutually_exclusive_group(required=True)
        target.add_argument("--output", help="Write the Coretax XML file here.")
        target.add_argument("--send", action="store_true", help="Send it through CORETAX_CONNECTOR.")

    def handle(self, *args, year, month, output=None, send=False, **options):
        invoices = coretax.month_invoices(year, month)
        if not invoices:
            self.stdout.write("No paid subscription invoice of that month needs a faktur (buyers without an NPWP are left out).")
            return
        xml = coretax.build_xml(invoices)
        if output:
            Path(output).write_bytes(xml)
            self.stdout.write(self.style.SUCCESS(f"Wrote {len(invoices)} faktur(s) to {output}."))
            return
        connector = platform_connector()
        if connector is None:
            raise CommandError("CORETAX_CONNECTOR is not set.")
        from django.conf import settings

        receipt = connector.submit(
            document_type=DocumentType.TAX_INVOICES, tin=coretax.npwp16(settings.BILLING_SELLER_NPWP),
            file_name=f"faktur-langganan-{year}-{month:02d}.xml", xml=xml,
        )
        submission = CoretaxSubmission.objects.create(
            organization=None, document_type=DocumentType.TAX_INVOICES, label=f"Langganan {year}-{month:02d}",
            connector=settings.CORETAX_CONNECTOR, reference=receipt.reference, status=receipt.status,
        )
        submission = apply_result(submission, connector.fetch(submission.reference))
        self.stdout.write(self.style.SUCCESS(f"Sent {len(invoices)} faktur(s): {submission.get_status_display()}."))
