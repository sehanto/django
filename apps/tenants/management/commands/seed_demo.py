"""Create a demo tenant with realistic MSME transactions.

The demo business data (names, journal descriptions) is Indonesian on purpose.

Usage: python manage.py seed_demo [--email demo@bukuumkm.id] [--password demo12345]
"""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.core.validators import validate_email
from django.db import transaction
from django.utils import timezone

from apps.contacts.models import Contact
from apps.invoicing import services as invoicing
from apps.invoicing.models import InvoiceKind, Item
from apps.ledger.models import Account
from apps.ledger.services import JournalLineInput, create_journal_entry
from apps.taxes.models import TaxRate
from apps.tenants.models import Plan
from apps.tenants.services import create_organization, set_subscription

D = Decimal


class Command(BaseCommand):
    help = "Create a demo organization with sample transactions."

    def add_arguments(self, parser):
        parser.add_argument("--email", default="demo@bukuumkm.id")
        parser.add_argument("--password", default="demo12345")

    @transaction.atomic
    def handle(self, *args, email, password, **options):
        try:
            validate_email(email)
        except ValidationError as exc:
            raise CommandError(f"Invalid email address: {email}") from exc
        User = get_user_model()
        if User.objects.filter(email=email).exists():
            raise CommandError(f"User {email} already exists.")
        user = User.objects.create_user(email=email, password=password, full_name="Rina Wulandari")
        org = create_organization(
            owner=user, name="Kopi Nusantara Rina", legal_form="perorangan", is_pkp=True,
            npwp="0123456789012345", address="Jl. Malioboro No. 12", city="Yogyakarta", phone="0812-3456-7890",
        )

        pro = Plan.objects.filter(code="pro").first()
        if pro:
            set_subscription(organization=org, plan=pro, paid_until=timezone.localdate() + timedelta(days=30))

        def acc(code):
            return Account.objects.get(organization=org, code=code)

        ppn = TaxRate.objects.filter(organization=org, dpp_numerator=11).first()
        today = timezone.localdate()
        start = (today.replace(day=1) - timedelta(days=150)).replace(day=1)

        create_journal_entry(
            organization=org, date=start, description="Setoran modal awal pemilik", created_by=user,
            lines=[JournalLineInput(acc("1-1100"), debit=D("75000000")), JournalLineInput(acc("3-1000"), credit=D("75000000"))],
        )
        create_journal_entry(
            organization=org, date=start + timedelta(days=2), description="Pembelian mesin espresso", created_by=user,
            lines=[JournalLineInput(acc("1-2300"), debit=D("28000000")), JournalLineInput(acc("1-1100"), credit=D("28000000"))],
        )

        customers = [
            Contact.objects.create(organization=org, kind="customer", name=name, payment_terms_days=terms, city=city, npwp=npwp)
            for name, terms, city, npwp in [
                ("PT Hotel Merapi Indah", 30, "Sleman", "0987654321098765"),
                ("Kafe Senja", 14, "Yogyakarta", ""),
                ("CV Oleh-Oleh Jogja", 7, "Bantul", ""),
            ]
        ]
        suppliers = [
            Contact.objects.create(organization=org, kind="supplier", name=name, payment_terms_days=30, city=city)
            for name, city in [("UD Petani Kopi Temanggung", "Temanggung"), ("Toko Kemasan Sejahtera", "Yogyakarta")]
        ]
        arabika = Item.objects.create(
            organization=org, code="KOPI-ARB", name="Kopi Arabika Gayo 1 kg", unit="kg",
            sale_price=D("185000"), purchase_price=D("120000"), income_account=acc("4-1000"),
            expense_account=acc("5-1000"), tax_rate=ppn,
        )
        Item.objects.create(
            organization=org, code="JASA-BARISTA", name="Pelatihan Barista", type="service", unit="sesi",
            sale_price=D("1500000"), income_account=acc("4-1100"), tax_rate=ppn,
        )

        for month in range(6):
            day = (start + timedelta(days=31 * month)).replace(day=5)
            if day > today:
                break
            bill = invoicing.create_invoice(
                organization=org, kind=InvoiceKind.PURCHASE, contact=suppliers[0], date=day, created_by=user,
                lines=[invoicing.InvoiceLineInput("Green bean arabika", acc("5-1000"), D("45") + month * 6, D("120000"), tax_rate=ppn, item=arabika)],
            )
            bill = invoicing.issue_invoice(invoice=bill, issued_by=user)
            if day + timedelta(days=20) <= today:
                invoicing.record_payment(invoice=bill, date=day + timedelta(days=20), amount=bill.total, cash_account=acc("1-1100"))

            for index, customer in enumerate(customers):
                sale_day = day + timedelta(days=3 + index * 4)
                if sale_day > today:
                    continue
                invoice = invoicing.create_invoice(
                    organization=org, kind=InvoiceKind.SALES, contact=customer, date=sale_day, created_by=user,
                    lines=[invoicing.InvoiceLineInput(arabika.name, acc("4-1000"), D("30") + month * 4 + index * 8, D("185000"), tax_rate=ppn, item=arabika)],
                )
                invoice = invoicing.issue_invoice(invoice=invoice, issued_by=user)
                paid_on = sale_day + timedelta(days=customer.payment_terms_days)
                if paid_on <= today - timedelta(days=10) and not (month >= 4 and index == 0):
                    invoicing.record_payment(invoice=invoice, date=paid_on, amount=invoice.total, cash_account=acc("1-1100"), method="transfer")

            for code, amount, label in [("6-1100", "2500000", "Sewa kios"), ("6-1000", "4500000", "Gaji karyawan"), ("6-1200", "850000", "Listrik & internet")]:
                when = day + timedelta(days=20)
                if when <= today:
                    create_journal_entry(
                        organization=org, date=when, description=label, created_by=user,
                        lines=[JournalLineInput(acc(code), debit=D(amount)), JournalLineInput(acc("1-1100"), credit=D(amount))],
                    )

        self.stdout.write(self.style.SUCCESS(f"Demo ready. Sign in with {email} / {password}"))
