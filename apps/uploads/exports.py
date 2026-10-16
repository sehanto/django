"""Contacts and items downloaded in the columns of their upload template, so a file can be edited
in a spreadsheet and uploaded again (rows already there are skipped, never overwritten)."""

from django.utils.translation import gettext as _

from .importers.master import ContactImporter, ItemImporter


def _header(importer) -> list[str]:
    return [column.label for column in importer.columns]


def contact_rows(contacts):
    columns = [column.key for column in ContactImporter.columns]
    yield _header(ContactImporter)
    for c in contacts.select_related("village"):
        values = {
            "name": c.name, "kind": c.kind, "npwp": c.npwp, "nitku": c.nitku, "id_type": c.id_type, "id_number": c.id_number,
            "country": c.country, "foreign_tin": c.foreign_tin, "email": c.email, "phone": c.phone, "address": c.address,
            "village": c.village_id or "", "postal_code": c.postal_code, "payment_terms_days": c.payment_terms_days,
            "document_language": c.document_language, "vat_collector": c.vat_collector, "notes": c.notes,
        }
        yield [values[key] for key in columns]


def item_rows(items):
    columns = [column.key for column in ItemImporter.columns]
    yield _header(ItemImporter)
    for i in items.select_related("income_account", "expense_account", "tax_rate", "inventory_account", "cogs_account"):
        values = {
            "name": i.name, "code": i.code, "type": i.type, "unit": i.unit, "sale_price": i.sale_price, "purchase_price": i.purchase_price,
            "income_account": i.income_account.code if i.income_account_id else "",
            "expense_account": i.expense_account.code if i.expense_account_id else "",
            "tax_rate": i.tax_rate.name if i.tax_rate_id else "", "track_inventory": _("yes") if i.track_inventory else _("no"),
            "inventory_account": i.inventory_account.code if i.inventory_account_id else "",
            "cogs_account": i.cogs_account.code if i.cogs_account_id else "",
        }
        yield [values[key] for key in columns]
