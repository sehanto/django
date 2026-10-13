from contextlib import contextmanager
from datetime import date
from decimal import Decimal

import pytest
from django.core.management import call_command
from django.db import IntegrityError, transaction

from apps.core.exceptions import ValidationFailed
from apps.inventory import services
from apps.inventory.models import StockMovement
from apps.inventory.selectors import reconcile, stock_card, stock_summary
from apps.invoicing import services as invoicing
from apps.invoicing.models import InvoiceKind
from apps.invoicing.services import InvoiceLineInput
from apps.ledger.selectors import account_balance
from apps.taxes.models import TaxRate

D = Decimal


def balance(account, code):
    acc = account(code)
    return account_balance(acc.organization, acc)


def use(organization, formula):
    organization.inventory_cost_formula = formula
    organization.save(update_fields=["inventory_cost_formula"])


def test_purchases_go_to_inventory_and_sales_post_their_fifo_cost(organization, account, coffee, buy, sell):
    use(organization, organization.InventoryCostFormula.FIFO)
    buy(coffee, "10", "100000")
    buy(coffee, "10", "150000", on=date(2026, 3, 5))
    assert balance(account, "1-1300") == D("2500000") and balance(account, "5-1000") == 0

    invoice = sell(coffee, "12")
    movement = StockMovement.objects.get(invoice_line__invoice=invoice)
    assert (movement.quantity, movement.value, movement.cost_formula) == (D("-12"), D("-1300000"), "fifo")
    assert balance(account, "5-1000") == D("1300000")  # in the sale's own journal entry
    assert invoice.journal_entry.lines.filter(account=account("5-1000"), debit=D("1300000")).exists()
    assert balance(account, "1-1300") == D("1200000")
    [row] = stock_summary(organization)
    assert (row.quantity, row.value, row.average_cost) == (D("8"), D("1200000"), D("150000"))


def test_weighted_average(organization, account, coffee, buy, sell):
    use(organization, organization.InventoryCostFormula.WEIGHTED_AVERAGE)
    buy(coffee, "10", "100000")
    buy(coffee, "10", "150000")
    sell(coffee, "12")
    assert balance(account, "5-1000") == D("1500000")  # 12 x 125.000
    assert stock_summary(organization)[0].average_cost == D("125000")


def test_selling_more_than_in_stock_is_refused(organization, coffee, buy, sell):
    buy(coffee, "2", "100000")
    with pytest.raises(ValidationFailed, match="Not enough Kopi Arabika in stock: 2 kg available"):
        sell(coffee, "3")
    assert StockMovement.objects.count() == 1  # the sale posted nothing


def test_purchase_vat_is_part_of_the_cost_without_pkp(organization, account, coffee, buy):
    ppn = TaxRate.objects.filter(organization=organization, dpp_numerator=11).first()
    buy(coffee, "10", "100000", tax_rate=ppn)  # PPN 11% can't be credited by a non-PKP
    assert StockMovement.objects.get().value == D("1110000")
    assert balance(account, "1-1300") == D("1110000")


def test_untracked_items_still_go_to_their_expense_account(organization, account, coffee, buy):
    coffee.track_inventory = False
    coffee.save()
    bill = invoicing.create_invoice(
        organization=organization, kind=InvoiceKind.PURCHASE, contact=bill_supplier(organization), date=date(2026, 3, 1),
        lines=[InvoiceLineInput("Kopi", account("5-1000"), D("1"), D("100000"), item=coffee)],
    )
    invoicing.issue_invoice(invoice=bill)
    assert balance(account, "5-1000") == D("100000") and not StockMovement.objects.exists()


def bill_supplier(organization):
    from apps.contacts.models import Contact

    return Contact.objects.filter(organization=organization, kind__in=[Contact.Kind.SUPPLIER, Contact.Kind.BOTH]).first()


def test_tracked_purchases_must_use_the_inventory_account(organization, account, coffee, supplier):
    with pytest.raises(ValidationFailed, match="inventory account"):
        invoicing.create_invoice(
            organization=organization, kind=InvoiceKind.PURCHASE, contact=supplier, date=date(2026, 3, 1),
            lines=[InvoiceLineInput("Kopi", account("5-1000"), D("1"), D("100000"), item=coffee)],
        )


def test_voiding_a_sale_returns_its_stock_at_its_cost(organization, account, coffee, buy, sell):
    buy(coffee, "10", "100000")
    invoice = sell(coffee, "4")
    invoicing.void_invoice(invoice=invoice, on=date(2026, 3, 11))
    assert balance(account, "5-1000") == 0 and balance(account, "1-1300") == D("1000000")
    returned = StockMovement.objects.get(kind=StockMovement.Kind.SALE_RETURN)
    assert (returned.quantity, returned.value, returned.date) == (D("4"), D("400000"), date(2026, 3, 11))
    assert stock_summary(organization)[0].quantity == D("10")


def test_a_purchase_can_be_voided_only_while_its_stock_is_untouched(organization, account, coffee, buy, sell):
    use(organization, organization.InventoryCostFormula.FIFO)
    first = buy(coffee, "10", "100000")
    second = buy(coffee, "5", "150000")
    sell(coffee, "4")  # FIFO: from the first bill
    invoicing.void_invoice(invoice=second, on=date(2026, 3, 12))
    assert balance(account, "1-1300") == D("600000")
    with pytest.raises(ValidationFailed, match="already left stock"):
        invoicing.void_invoice(invoice=first, on=date(2026, 3, 12))


def test_stock_adjustments(organization, account, coffee, user):
    opening = services.adjust_stock(
        organization=organization, item=coffee, on=date(2026, 1, 1), quantity=D("20"), unit_cost=D("110000"),
        counter_account=account("3-1000"), reason="Stok awal", created_by=user,
    )
    assert opening.value == D("2200000") and opening.journal_entry.number.startswith("JI/")
    assert balance(account, "1-1300") == D("2200000") and balance(account, "3-1000") == D("2200000")
    services.adjust_stock(
        organization=organization, item=coffee, on=date(2026, 1, 31), quantity=D("-1.5"),
        counter_account=account("5-1000"), reason="Susut saat stock opname",
    )
    assert balance(account, "5-1000") == D("165000")
    card = stock_card(coffee)
    assert [(r.quantity, r.value) for r in card] == [(D("20"), D("2200000")), (D("18.5"), D("2035000"))]

    with pytest.raises(ValidationFailed, match="not an inventory account"):
        services.adjust_stock(organization=organization, item=coffee, on=date(2026, 2, 1), quantity=D("1"), counter_account=account("1-1300"), reason="x")
    with pytest.raises(ValidationFailed, match="Not enough"):
        services.adjust_stock(organization=organization, item=coffee, on=date(2026, 2, 1), quantity=D("-100"), counter_account=account("5-1000"), reason="x")


def test_stock_reconciles_with_the_ledger(organization, account, coffee, buy):
    from apps.ledger.services import JournalLineInput, create_journal_entry

    buy(coffee, "10", "100000")
    [row] = reconcile(organization, stock_summary(organization), date(2026, 12, 31))
    assert (row.stock, row.ledger, row.difference) == (D("1000000"), D("1000000"), 0)
    create_journal_entry(  # inventory posted outside the stock card
        organization=organization, date=date(2026, 3, 2), description="Koreksi",
        lines=[JournalLineInput(account("1-1300"), debit=D("5000")), JournalLineInput(account("1-1000"), credit=D("5000"))],
    )
    assert reconcile(organization, stock_summary(organization), date(2026, 12, 31))[0].difference == D("5000")


@contextmanager
def refused():
    with pytest.raises(IntegrityError), transaction.atomic():
        yield


def test_the_stock_card_and_tracking_are_fixed_in_the_database(organization, account, coffee, buy):
    buy(coffee, "10", "100000")
    movement = StockMovement.objects.get()
    with refused():
        StockMovement.objects.filter(pk=movement.pk).update(value=D("1"))
    with refused():
        movement.delete()
    for change in ({"track_inventory": False}, {"inventory_account": account("1-1600")}, {"cogs_account": account("6-1900")}):
        with refused():
            type(coffee).objects.filter(pk=coffee.pk).update(**change)
    type(coffee).objects.filter(pk=coffee.pk).update(name="Kopi Gayo", sale_price=D("190000"))  # the rest stays editable


def test_deleting_an_organization_removes_its_stock(organization, coffee, buy, sell):
    buy(coffee, "10", "100000")
    sell(coffee, "2")
    call_command("delete_organization", str(organization.pk), "--yes", "--skip-export")
    assert not StockMovement.objects.exists()
