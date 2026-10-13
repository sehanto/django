"""Sales and purchase returns (nota retur)."""

from contextlib import contextmanager
from datetime import date
from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import translation

from apps.core.exceptions import InvalidStateTransition, ValidationFailed
from apps.inventory.models import StockMovement
from apps.inventory.selectors import stock_summary
from apps.inventory.services import adjust_stock
from apps.invoicing import services
from apps.invoicing.models import Invoice, InvoiceKind, Item, ReturnLine, ReturnNote
from apps.invoicing.selectors import vat_summary
from apps.invoicing.services import InvoiceLineInput, ReturnLineInput
from apps.ledger.selectors import account_balance
from apps.taxes.models import TaxRate
from apps.taxes.selectors import monthly_gross_turnover

D = Decimal
ON = date(2026, 3, 10)


@pytest.fixture(autouse=True)
def english():
    with translation.override("en"):
        yield


def balance(account, code):
    acc = account(code)
    return account_balance(acc.organization, acc)


def invoice(organization, contact, account, kind=InvoiceKind.SALES, quantity="10", price="100000", tax_rate=None, item=None, code=None):
    code = code or ("4-1000" if kind == InvoiceKind.SALES else "5-1000")
    draft = services.create_invoice(
        organization=organization, kind=kind, contact=contact, date=ON,
        lines=[InvoiceLineInput("Kopi", account(code), D(quantity), D(price), tax_rate=tax_rate, item=item)],
    )
    return services.issue_invoice(invoice=draft)


def give_back(inv, quantity, on=date(2026, 3, 20), **kwargs):
    line = inv.lines.get()
    return services.create_return(invoice=inv, date=on, reason="Rusak", lines=[ReturnLineInput(line, D(quantity))], **kwargs)


def test_a_sales_return_credits_the_invoice(organization, customer, account):
    inv = invoice(organization, customer, account)
    note = give_back(inv, "3")
    assert note.number.startswith("RJ/2026/03/") and (note.total, note.credited, note.refunded) == (D("300000"), D("300000"), 0)
    assert balance(account, "4-1000") == D("700000") and balance(account, "1-1200") == D("700000")
    inv.refresh_from_db()
    assert (inv.amount_credited, inv.balance_due, inv.status) == (D("300000"), D("700000"), Invoice.Status.PARTIAL)
    assert monthly_gross_turnover(organization, 2026)[2] == D("700000")  # PPh Final turnover is net of returns
    assert inv.status_label == "Partly returned"  # nothing has been paid

    services.record_payment(invoice=inv, date=ON, amount=D("700000"), cash_account=account("1-1000"))
    inv.refresh_from_db()
    assert inv.status == Invoice.Status.PAID


def test_returning_a_paid_sale_refunds_the_customer(organization, customer, account):
    inv = invoice(organization, customer, account)
    services.record_payment(invoice=inv, date=ON, amount=D("600000"), cash_account=account("1-1000"))
    with pytest.raises(ValidationFailed, match="refunded"):
        give_back(inv, "5")
    note = give_back(inv, "5", cash_account=account("1-1000"))
    assert (note.credited, note.refunded) == (D("400000"), D("100000"))  # 400.000 was still owed
    assert balance(account, "1-1000") == D("500000") and balance(account, "1-1200") == 0
    inv.refresh_from_db()
    assert inv.status == Invoice.Status.PAID and inv.balance_due == 0


def test_ppn_is_corrected_in_the_month_of_the_return(pkp_organization, customer, account):
    ppn = TaxRate.objects.filter(organization=pkp_organization, dpp_numerator=11).first()
    inv = invoice(pkp_organization, customer, account, tax_rate=ppn)  # 1.000.000 + PPN 110.000
    note = give_back(inv, "4", on=date(2026, 4, 2))
    assert (note.subtotal, note.tax_amount) == (D("400000"), D("44000"))
    assert balance(account, "2-1100") == D("66000")
    months = {m["month"]: m for m in vat_summary(pkp_organization, 2026)}
    assert (months[3]["output"], months[4]["output"], months[4]["output_base"]) == (D("110000"), D("-44000"), D("-400000"))


def test_quantities_are_limited_to_what_was_invoiced(organization, customer, account):
    inv = invoice(organization, customer, account)
    give_back(inv, "7")
    with pytest.raises(ValidationFailed, match="At most 3"):
        give_back(inv, "4")
    give_back(inv, "3")
    inv.refresh_from_db()
    assert inv.status == Invoice.Status.PAID and inv.balance_due == 0 and inv.status_label == "Returned"  # settled by returns


def test_returns_are_validated(organization, customer, account):
    inv = invoice(organization, customer, account)
    with pytest.raises(ValidationFailed, match="before the invoice date"):
        give_back(inv, "1", on=date(2026, 3, 1))
    with pytest.raises(ValidationFailed, match="at least one line"):
        give_back(inv, "0")
    draft = services.create_invoice(
        organization=organization, kind=InvoiceKind.SALES, contact=customer, date=ON,
        lines=[InvoiceLineInput("Kopi", account("4-1000"), D("1"), D("1000"))],
    )
    with pytest.raises(InvalidStateTransition):
        services.create_return(invoice=draft, date=ON, reason="x", lines=[ReturnLineInput(draft.lines.get(), D("1"))])


def test_an_invoice_with_returns_cannot_be_voided(organization, customer, account):
    inv = invoice(organization, customer, account)
    give_back(inv, "1")
    with pytest.raises(InvalidStateTransition, match="has returns"):
        services.void_invoice(invoice=inv, on=ON)


def test_a_purchase_return_without_pkp_takes_the_ppn_back_out_of_the_cost(organization, supplier, account):
    ppn = TaxRate.objects.filter(organization=organization, dpp_numerator=11).first()
    bill = invoice(organization, supplier, account, kind=InvoiceKind.PURCHASE, tax_rate=ppn)  # cost 1.110.000
    note = give_back(bill, "10")
    assert note.number.startswith("RB/") and note.total == D("1110000")
    assert balance(account, "5-1000") == 0 and balance(account, "2-1000") == 0


@pytest.fixture
def coffee(organization, account):
    return Item.objects.create(
        organization=organization, name="Kopi", unit="kg", income_account=account("4-1000"), track_inventory=True,
        inventory_account=account("1-1300"), cogs_account=account("5-1000"),
    )


def test_returned_sales_come_back_into_stock_at_their_cost(organization, customer, account, coffee):
    adjust_stock(organization=organization, item=coffee, on=date(2026, 1, 1), quantity=D("3"), unit_cost=D("10000"),
                 counter_account=account("3-1000"), reason="Stok awal")
    inv = invoice(organization, customer, account, quantity="3", price="20000", item=coffee)
    assert balance(account, "5-1000") == D("30000")
    give_back(inv, "1")
    give_back(inv, "2")
    returns = StockMovement.objects.filter(kind=StockMovement.Kind.SALE_RETURN)
    assert [m.value for m in returns] == [D("10000"), D("20000")] and all(m.return_line_id for m in returns)
    assert balance(account, "5-1000") == 0 and balance(account, "1-1300") == D("30000")
    assert stock_summary(organization)[0].quantity == D("3")


def test_goods_returned_to_the_supplier_leave_stock_at_their_stock_cost(organization, supplier, account, coffee):
    organization.inventory_cost_formula = organization.InventoryCostFormula.WEIGHTED_AVERAGE
    organization.save(update_fields=["inventory_cost_formula"])
    adjust_stock(organization=organization, item=coffee, on=date(2026, 1, 1), quantity=D("10"), unit_cost=D("8000"),
                 counter_account=account("3-1000"), reason="Stok awal")
    bill = invoice(organization, supplier, account, kind=InvoiceKind.PURCHASE, quantity="10", price="12000", item=coffee, code="1-1300")
    give_back(bill, "5")  # average 10.000: 50.000 leaves stock, the bill's 60.000 is credited
    movement = StockMovement.objects.get(kind=StockMovement.Kind.PURCHASE_RETURN)
    assert (movement.quantity, movement.value) == (D("-5"), D("-50000"))
    assert balance(account, "1-1300") == D("150000")
    assert balance(account, "5-1000") == D("-10000")  # the difference lowers cost of goods sold
    assert balance(account, "2-1000") == D("60000")


@contextmanager
def refused():
    with pytest.raises(IntegrityError), transaction.atomic():
        yield


def test_returns_are_final_in_the_database(organization, customer, account):
    inv = invoice(organization, customer, account)
    note = give_back(inv, "2")
    with refused():
        ReturnNote.objects.filter(pk=note.pk).update(total=D("1"))
    with refused():
        note.delete()
    with refused():
        ReturnLine.objects.filter(return_note=note).update(quantity=D("1"))
    with refused():  # nothing can be added to a posted return
        ReturnLine.objects.create(return_note=note, invoice_line=inv.lines.get(), quantity=D("1"), amount=D("100000"))
    with refused():  # the invoice must show what its returns credited
        Invoice.objects.filter(pk=inv.pk).update(amount_credited=0, status=Invoice.Status.ISSUED)


def test_recording_a_return_through_the_pages(auth_client, organization, customer, account, user):
    inv = invoice(organization, customer, account)
    url = reverse("invoicing:return_create", args=[InvoiceKind.SALES, inv.pk])
    form = auth_client.get(url).context["form"]
    line = inv.lines.get()
    response = auth_client.post(url, {"date": "2026-03-20", "reason": "Rusak", f"line_{line.pk}": "2", "cash_account": ""})
    note = ReturnNote.objects.get(invoice=inv)
    assert response.url == reverse("invoicing:return_detail", args=[note.pk])
    assert note.lines.get().quantity == D("2") and note.created_by == user
    page = auth_client.get(reverse("invoicing:detail", args=[InvoiceKind.SALES, inv.pk])).content.decode()
    assert note.number in page
    detail = auth_client.get(reverse("invoicing:return_detail", args=[note.pk]))
    assert detail.status_code == 200 and "Rusak" in detail.content.decode()
    assert form.fields[f"line_{line.pk}"].max_value == D("10")

    response = auth_client.post(url, {"date": "2026-03-20", "reason": "Lagi", f"line_{line.pk}": "9"})
    assert response.status_code == 200 and ReturnNote.objects.count() == 1  # only 8 left


def test_deleting_an_organization_removes_its_returns(organization, customer, account):
    from django.core.management import call_command

    give_back(invoice(organization, customer, account), "2")
    call_command("delete_organization", str(organization.pk), "--yes", "--skip-export")
    assert not ReturnNote.objects.exists() and not ReturnLine.objects.exists()


# --- Voiding a return ----------------------------------------------------------------


def test_voiding_a_return_restores_the_invoice_and_the_books(organization, customer, account):
    inv = invoice(organization, customer, account)
    services.record_payment(invoice=inv, date=ON, amount=D("600000"), cash_account=account("1-1000"))
    note = give_back(inv, "5", cash_account=account("1-1000"))  # 400.000 credited, 100.000 refunded
    services.void_return(note=note, on=date(2026, 3, 25))
    note.refresh_from_db()
    inv.refresh_from_db()
    assert note.status == ReturnNote.Status.VOID
    assert (inv.amount_credited, inv.balance_due, inv.status) == (0, D("400000"), Invoice.Status.PARTIAL)
    assert balance(account, "4-1000") == D("1000000") and balance(account, "1-1000") == D("600000")
    with pytest.raises(InvalidStateTransition, match="already been voided"):
        services.void_return(note=note, on=date(2026, 3, 25))

    give_back(inv, "10", cash_account=account("1-1000"))  # the voided return no longer counts
    assert monthly_gross_turnover(organization, 2026)[2] == 0


def test_a_voided_return_leaves_the_ppn_summary_and_frees_the_invoice(pkp_organization, customer, account):
    ppn = TaxRate.objects.filter(organization=pkp_organization, dpp_numerator=11).first()
    inv = invoice(pkp_organization, customer, account, tax_rate=ppn)
    note = give_back(inv, "4", on=date(2026, 4, 2))
    services.void_return(note=note, on=date(2026, 4, 3))
    assert {m["month"]: m for m in vat_summary(pkp_organization, 2026)}[4]["output"] == 0
    services.void_invoice(invoice=inv, on=date(2026, 4, 3))  # no posted returns left


def test_voiding_a_sales_return_takes_its_stock_out_again(organization, customer, account, coffee):
    adjust_stock(organization=organization, item=coffee, on=date(2026, 1, 1), quantity=D("3"), unit_cost=D("10000"),
                 counter_account=account("3-1000"), reason="Stok awal")
    inv = invoice(organization, customer, account, quantity="3", price="20000", item=coffee)
    note = give_back(inv, "2")
    services.void_return(note=note, on=date(2026, 3, 21))
    voided = StockMovement.objects.get(kind=StockMovement.Kind.SALE_RETURN_VOIDED)
    assert (voided.quantity, voided.value) == (D("-2"), D("-20000"))
    assert balance(account, "5-1000") == D("30000") and stock_summary(organization)[0].quantity == 0
    assert note.number in stock_card_page(organization, coffee)

    again = give_back(inv, "2")
    sell = services.create_invoice(
        organization=organization, kind=InvoiceKind.SALES, contact=customer, date=ON,
        lines=[InvoiceLineInput("Kopi", account("4-1000"), D("1"), D("20000"), item=coffee)],
    )
    services.issue_invoice(invoice=sell)  # weighted average: merges the returned layer
    with pytest.raises(ValidationFailed, match="already left stock again"):
        services.void_return(note=again, on=date(2026, 3, 22))


def test_voiding_a_purchase_return_brings_its_stock_back(organization, supplier, account, coffee):
    bill = invoice(organization, supplier, account, kind=InvoiceKind.PURCHASE, quantity="10", price="12000", item=coffee, code="1-1300")
    note = give_back(bill, "4")
    services.void_return(note=note, on=date(2026, 3, 21))
    back = StockMovement.objects.get(kind=StockMovement.Kind.PURCHASE_RETURN_VOIDED)
    assert (back.quantity, back.value) == (D("4"), D("48000"))
    assert balance(account, "1-1300") == D("120000") and balance(account, "2-1000") == D("120000")


def test_voiding_through_the_page(auth_client, organization, customer, account):
    inv = invoice(organization, customer, account)
    note = give_back(inv, "2")
    auth_client.post(reverse("invoicing:return_void", args=[note.pk]), {"date": "2026-03-21"})
    note.refresh_from_db()
    assert note.status == ReturnNote.Status.VOID
    with refused():  # void is final
        ReturnNote.objects.filter(pk=note.pk).update(status=ReturnNote.Status.POSTED)


def stock_card_page(organization, item):
    from django.test import Client

    from apps.tenants.models import Membership

    client = Client()
    client.force_login(Membership.objects.filter(organization=organization).first().user)
    with translation.override("en"):  # the request activates the user's language; keep the test's
        return client.get(reverse("inventory:card", args=[item.pk])).content.decode()
