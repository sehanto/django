"""Contacts, items and accounts from a spreadsheet, checked by the same forms as their pages.

A row that is already there (a contact with the same NPWP and NITKU, or the same name; an item
with the same code, or the same name; an account with the same code) is skipped, never
overwritten, so a file can be uploaded again after adding rows to it.
"""

from django.conf import settings
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from apps.audit import services as audit
from apps.audit.domain import AuditAction
from apps.contacts import services as contact_services
from apps.contacts.forms import ContactForm
from apps.contacts.models import BuyerIdType, Contact, VatCollector
from apps.contacts.selectors import contacts_for
from apps.invoicing import services as invoicing_services
from apps.invoicing.forms import ItemForm
from apps.invoicing.models import Item
from apps.invoicing.selectors import items_for
from apps.ledger import services as ledger_services
from apps.ledger.domain import AccountCategory
from apps.ledger.forms import AccountForm
from apps.ledger.selectors import accounts_for
from apps.regions.services import CODE
from apps.taxes.selectors import tax_rates_for

from ..domain import Column, RowOutcome, digits, normalized_header
from .base import Importer, RowError, form_errors, labels


def village_code(text: str) -> str:
    """A Kemendagri village code as written (``36.03.22.2008``) or as its 10 digits."""
    value = text.strip()
    if not value:
        return ""
    number = digits(value)
    if len(number) == 10 and not CODE.match(value):
        value = f"{number[:2]}.{number[2:4]}.{number[4:6]}.{number[6:]}"
    if not CODE.match(value) or value.count(".") != 3:
        raise RowError(_("Kelurahan/desa code: %(value)s is not a 10-digit Kemendagri code such as 36.03.22.2008.") % {"value": text})
    return value


class ContactImporter(Importer):
    kind = "contacts"
    group = "master"
    title = gettext_lazy("Contacts")
    description = gettext_lazy(
        "Customers and suppliers with their NPWP and address. The kelurahan/desa is its Kemendagri code, or found from "
        "the postcode when only one has it; the postcode is checked against it."
    )
    columns = (
        Column("name", "name", ("nama", "nama kontak", "nama pelanggan", "nama pemasok"), required=True),
        Column("kind", "type", ("jenis", "tipe")),
        Column("npwp", "npwp", ("tax id", "nomor npwp")),
        Column("nitku", "nitku"),
        Column("id_type", "identity document", ("jenis identitas", "jenis dokumen")),
        Column("id_number", "identity number", ("nomor identitas", "nik", "nomor dokumen")),
        Column("country", "country", ("negara", "kode negara")),
        Column("foreign_tin", "foreign tax id", ("tin luar negeri",)),
        Column("email", "email", ("e-mail", "surel")),
        Column("phone", "phone", ("telepon", "no telepon", "nomor telepon", "hp")),
        Column("address", "address", ("alamat", "jalan")),
        Column("village", "village code", ("kode desa", "kode kelurahan", "kode wilayah", "kelurahan/desa")),
        Column("postal_code", "postcode", ("kode pos", "postal code")),
        Column("payment_terms_days", "payment terms (days)", ("termin", "termin pembayaran", "jatuh tempo (hari)")),
        Column("document_language", "document language", ("bahasa dokumen", "bahasa")),
        Column("vat_collector", "VAT collector", ("pemungut ppn",)),
        Column("notes", "notes", ("catatan", "keterangan")),
    )
    example = (
        {"name": "PT Contoh Pelanggan", "kind": "customer", "npwp": "0012345678901000", "email": "keuangan@contoh.co.id",
         "address": "Jl. Raya Serpong No. 1", "village": "36.03.22.2008", "postal_code": "15330", "payment_terms_days": "30"},
        {"name": "CV Pemasok Bahan", "kind": "supplier", "phone": "0812-3456-7890", "address": "Jl. Pasar Baru 5", "postal_code": "15330"},
    )
    KIND_NAMES = (("pelanggan", "customer"), ("pembeli", "customer"), ("pemasok", "supplier"), ("vendor", "supplier"),
                  ("keduanya", "both"))

    def row_label(self, row):
        return row.get("name")

    def _kind(self, row) -> str:
        names = labels(Contact.Kind.choices)
        names = [(value, found | {alias for alias, target in self.KIND_NAMES if target == value}) for value, found in names]
        return self.choice(row, "kind", _("type"), names, default=Contact.Kind.CUSTOMER)

    def _existing(self, organization, name: str, npwp: str, nitku: str) -> Contact | None:
        contacts = contacts_for(organization)
        if npwp:
            same = contacts.filter(npwp=npwp, nitku=nitku).first()
            if same is not None:
                return same
        return contacts.filter(name__iexact=name).first()

    def apply_row(self, context, row):
        organization = context.organization
        name = row.get("name")
        npwp, nitku = digits(row.get("npwp")), digits(row.get("nitku"))
        existing = self._existing(organization, name, npwp, nitku)
        if existing is not None:
            return RowOutcome.SKIPPED, name, _("Already in your contacts as %(name)s.") % {"name": existing.name}
        id_type = self.choice(row, "id_type", _("identity document"), [
            *labels(BuyerIdType.choices), (BuyerIdType.NATIONAL_ID, {"nik", "ktp"}), (BuyerIdType.PASSPORT, {"paspor"}),
        ])
        data = {
            "kind": self._kind(row), "name": name, "npwp": npwp, "nitku": nitku, "id_type": id_type,
            "id_number": row.get("id_number"), "country": row.get("country"), "foreign_tin": row.get("foreign_tin"),
            "treaty_document": "", "email": row.get("email"), "phone": row.get("phone"), "address": row.get("address"),
            "village": village_code(row.get("village")), "postal_code": digits(row.get("postal_code")),
            "payment_terms_days": row.get("payment_terms_days") or "30",
            "document_language": self.choice(row, "document_language", _("document language"), labels(settings.LANGUAGES),
                                             default=settings.LANGUAGE_CODE),
            "vat_collector": self.choice(row, "vat_collector", _("VAT collector"), labels(VatCollector.choices)),
            "notes": row.get("notes"), "is_active": "on",
        }
        form = ContactForm(data, organization=organization)
        if not form.is_valid():
            raise RowError(form_errors(form))
        contact = contact_services.create_contact(organization=organization, **form.cleaned_data)
        return RowOutcome.CREATED, contact.name, contact.region_line


class ItemImporter(Importer):
    kind = "items"
    group = "master"
    title = gettext_lazy("Items")
    description = gettext_lazy(
        "Goods and services with their prices. Accounts are given by their code and the PPN by the name of one of your "
        "PPN rates; left empty, the item uses the defaults of the invoice lines."
    )
    columns = (
        Column("name", "name", ("nama", "nama barang", "nama item", "nama produk"), required=True),
        Column("code", "code", ("kode", "kode barang", "sku")),
        Column("type", "type", ("jenis", "tipe")),
        Column("unit", "unit", ("satuan",)),
        Column("sale_price", "sale price", ("harga jual",)),
        Column("purchase_price", "purchase price", ("harga beli",)),
        Column("income_account", "revenue account", ("akun pendapatan",)),
        Column("expense_account", "purchase account", ("akun pembelian", "akun beban")),
        Column("tax_rate", "PPN rate", ("tarif ppn", "ppn", "pajak")),
        Column("track_inventory", "track stock", ("lacak stok", "stok")),
        Column("inventory_account", "inventory account", ("akun persediaan",)),
        Column("cogs_account", "cost of goods sold account", ("akun hpp", "akun beban pokok")),
    )
    example = (
        {"name": "Kopi bubuk 250 g", "code": "KOPI-250", "type": "goods", "unit": "pcs", "sale_price": "45000", "purchase_price": "30000"},
        {"name": "Jasa instalasi", "code": "JASA-01", "type": "service", "unit": "jam", "sale_price": "150000"},
    )
    TYPE_NAMES = ((Item.Type.GOODS, {"barang"}), (Item.Type.SERVICE, {"jasa"}))

    def row_label(self, row):
        return row.get("name")

    def _account(self, context, row, key: str, label: str):
        code = row.get(key)
        if not code:
            return ""
        account = accounts_for(context.organization).filter(code=code).first()
        if account is None:
            raise RowError(_("%(column)s: there is no account %(code)s.") % {"column": label, "code": code})
        return account.pk

    def _tax_rate(self, context, row):
        name = row.get("tax_rate")
        if not name:
            return ""
        wanted = normalized_header(name)
        for rate in tax_rates_for(context.organization, active_only=True):
            if normalized_header(rate.name) == wanted:
                return rate.pk
        raise RowError(_("PPN rate: there is no active rate named %(name)s.") % {"name": name})

    def apply_row(self, context, row):
        organization = context.organization
        name, code = row.get("name"), row.get("code")
        items = items_for(organization)
        existing = (items.filter(code=code).first() if code else None) or items.filter(name__iexact=name).first()
        if existing is not None:
            return RowOutcome.SKIPPED, name, _("Already in your items as %(item)s.") % {"item": existing}
        types = [(value, names | extra) for (value, names), (_v, extra) in zip(labels(Item.Type.choices), self.TYPE_NAMES, strict=True)]
        data = {
            "name": name, "code": code, "type": self.choice(row, "type", _("type"), types, default=Item.Type.GOODS),
            "unit": row.get("unit") or "pcs",
            "sale_price": self.amount(row, "sale_price", _("sale price")) or 0,
            "purchase_price": self.amount(row, "purchase_price", _("purchase price")) or 0,
            "income_account": self._account(context, row, "income_account", _("revenue account")),
            "expense_account": self._account(context, row, "expense_account", _("purchase account")),
            "tax_rate": self._tax_rate(context, row),
            "inventory_account": self._account(context, row, "inventory_account", _("inventory account")),
            "cogs_account": self._account(context, row, "cogs_account", _("cost of goods sold account")),
            "is_active": "on",
        }
        if self.flag(row, "track_inventory", _("track stock")):
            data["track_inventory"] = "on"
        form = ItemForm(data, organization=organization)
        if not form.is_valid():
            raise RowError(form_errors(form))
        item = invoicing_services.create_item(organization=organization, **form.cleaned_data)
        return RowOutcome.CREATED, str(item), ""


class AccountImporter(Importer):
    kind = "accounts"
    group = "master"
    title = gettext_lazy("Accounts")
    description = gettext_lazy(
        "Accounts to add to the chart of accounts, e.g. one per bank account. The category is one of the SAK EMKM groups "
        "(as named on the account page, in either language); accounts whose code is already used are left as they are."
    )
    columns = (
        Column("code", "code", ("kode", "kode akun", "no akun", "nomor akun"), required=True),
        Column("name", "name", ("nama", "nama akun"), required=True),
        Column("category", "category", ("kategori", "kelompok"), required=True),
        Column("is_contra", "contra account", ("akun kontra", "kontra")),
        Column("description", "description", ("keterangan", "deskripsi")),
    )
    example = (
        {"code": "1-1110", "name": "Bank BCA 123-456-7890", "category": "cash_bank"},
        {"code": "6-1910", "name": "Beban Langganan Perangkat Lunak", "category": "operating_expense"},
    )

    def row_label(self, row):
        return f"{row.get('code')} {row.get('name')}".strip()

    def apply_row(self, context, row):
        organization = context.organization
        code = row.get("code")
        existing = accounts_for(organization).filter(code=code).first()
        if existing is not None:
            return RowOutcome.SKIPPED, str(existing), _("The code is already used by %(account)s.") % {"account": existing}
        data = {
            "code": code, "name": row.get("name"),
            "category": self.choice(row, "category", _("category"), labels(AccountCategory.choices)),
            "description": row.get("description"), "is_active": "on",
        }
        if self.flag(row, "is_contra", _("contra account")):
            data["is_contra"] = "on"
        form = AccountForm(data, organization=organization)
        if not form.is_valid():
            raise RowError(form_errors(form))
        account = ledger_services.create_account(organization=organization, **form.cleaned_data)
        audit.record(AuditAction.ACCOUNT_CREATED, request=context.request, organization=organization, target=str(account))
        return RowOutcome.CREATED, str(account), account.get_category_display()
