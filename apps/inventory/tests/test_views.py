from datetime import date
from decimal import Decimal

from django.urls import reverse

from apps.invoicing.models import InvoiceKind, Item
from apps.inventory.models import StockMovement
from apps.tenants.roles import Role

D = Decimal


def item_form(account, **overrides):
    return {
        "code": "GULA", "name": "Gula aren", "type": Item.Type.GOODS, "unit": "kg", "sale_price": "30000", "purchase_price": "20000",
        "income_account": account("4-1000").pk, "expense_account": account("5-1000").pk, "tax_rate": "",
        "track_inventory": "on", "inventory_account": account("1-1300").pk, "cogs_account": account("5-1000").pk,
        "is_active": "on", **overrides,
    }


def test_tracking_is_set_on_the_item_and_fixed_once_stocked(auth_client, organization, account, coffee, buy):
    auth_client.post(reverse("invoicing:item_create"), item_form(account))
    assert Item.objects.get(code="GULA").track_inventory

    response = auth_client.post(reverse("invoicing:item_create"), item_form(account, code="JASA", type=Item.Type.SERVICE))
    assert "track_inventory" in response.context["form"].errors  # services have no stock

    buy(coffee, "1", "100000")
    form = auth_client.get(reverse("invoicing:item_update", args=[coffee.pk])).context["form"]
    assert form.fields["track_inventory"].disabled and form.fields["inventory_account"].disabled
    assert not form.fields["sale_price"].disabled


def test_existing_items_get_the_suggested_stock_accounts(auth_client, organization, account):
    item = Item.objects.create(organization=organization, name="Gula", expense_account=account("5-1000"))
    form = auth_client.get(reverse("invoicing:item_update", args=[item.pk])).context["form"]
    assert (form.initial["inventory_account"], form.initial["cogs_account"]) == (account("1-1300"), account("5-1000"))


def test_purchase_lines_default_to_the_inventory_account(auth_client, coffee, account):
    page = auth_client.get(reverse("invoicing:create", args=[InvoiceKind.PURCHASE]))
    assert page.context["items"][coffee.pk]["account"] == account("1-1300").pk
    page = auth_client.get(reverse("invoicing:create", args=[InvoiceKind.SALES]))
    assert page.context["items"][coffee.pk]["account"] == account("4-1000").pk


def test_adjust_and_see_the_stock(auth_client, organization, account, coffee):
    response = auth_client.post(reverse("inventory:adjust"), {
        "item": coffee.pk, "on": "2026-01-01", "quantity": "5", "unit_cost": "100000",
        "counter_account": account("3-1000").pk, "reason": "Stok awal",
    })
    assert response.url == reverse("inventory:card", args=[coffee.pk])
    listing = auth_client.get(reverse("inventory:list"))
    assert listing.context["total_value"] == D("500000")
    card = auth_client.get(reverse("inventory:card", args=[coffee.pk])).content.decode()
    assert StockMovement.objects.get().journal_entry.number in card

    response = auth_client.post(reverse("inventory:adjust"), {
        "item": coffee.pk, "on": "2026-01-02", "quantity": "-50", "counter_account": account("5-1000").pk, "reason": "x",
    })
    assert response.status_code == 200 and response.context["form"].non_field_errors()


def test_viewers_see_the_stock_but_do_not_adjust(client, make_member, coffee, account):
    client.force_login(make_member(Role.VIEWER))
    assert client.get(reverse("inventory:card", args=[coffee.pk])).status_code == 200
    assert client.post(reverse("inventory:adjust"), {"item": coffee.pk}).status_code == 403


def test_other_organizations_items_are_not_found(auth_client, other_organization):
    from apps.ledger.models import Account

    def acc(code):
        return Account.objects.get(organization=other_organization, code=code)

    other = Item.objects.create(
        organization=other_organization, name="x", track_inventory=True, inventory_account=acc("1-1300"), cogs_account=acc("5-1000"),
    )
    assert auth_client.get(reverse("inventory:card", args=[other.pk])).status_code == 404
    response = auth_client.post(reverse("inventory:adjust"), {"item": other.pk, "on": date(2026, 1, 1), "quantity": "1"})
    assert "item" in response.context["form"].errors
