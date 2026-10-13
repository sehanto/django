from datetime import date
from decimal import Decimal

import pytest
from django.utils import translation

from apps.invoicing import services as invoicing
from apps.invoicing.models import InvoiceKind, Item
from apps.invoicing.services import InvoiceLineInput

D = Decimal


@pytest.fixture(autouse=True)
def english():
    """Messages are matched in English; the default language is Indonesian."""
    with translation.override("en"):
        yield


@pytest.fixture
def coffee(organization, account):
    return Item.objects.create(
        organization=organization, code="KOPI", name="Kopi Arabika", unit="kg", sale_price=D("185000"), purchase_price=D("120000"),
        income_account=account("4-1000"), expense_account=account("5-1000"), track_inventory=True,
        inventory_account=account("1-1300"), cogs_account=account("5-1000"),
    )


@pytest.fixture
def buy(organization, supplier, account):
    def buy(item, quantity, unit_price, on=date(2026, 3, 1), tax_rate=None):
        bill = invoicing.create_invoice(
            organization=organization, kind=InvoiceKind.PURCHASE, contact=supplier, date=on,
            lines=[InvoiceLineInput(item.name, item.inventory_account, D(quantity), D(unit_price), tax_rate=tax_rate, item=item)],
        )
        return invoicing.issue_invoice(invoice=bill)

    return buy


@pytest.fixture
def sell(organization, customer, account):
    def sell(item, quantity, unit_price="185000", on=date(2026, 3, 10)):
        invoice = invoicing.create_invoice(
            organization=organization, kind=InvoiceKind.SALES, contact=customer, date=on,
            lines=[InvoiceLineInput(item.name, item.income_account, D(quantity), D(unit_price), item=item)],
        )
        return invoicing.issue_invoice(invoice=invoice)

    return sell
