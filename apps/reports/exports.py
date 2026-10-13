"""Export of an organization's complete books: a ZIP of CSV files plus a README.

For the customer's own records (UU KUP Art. 28(11): books are kept for 10
years), moving to another system, or before the organization is deleted.
Headers come from the models' verbose names in the active language; stored
business data is exported as recorded.
"""

import csv
import io
import zipfile
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import date, datetime

from django.db import connection, transaction
from django.db.models import Model, QuerySet
from django.utils import timezone
from django.utils.text import capfirst, slugify
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from apps.contacts.models import Contact
from apps.core.sanitizers import csv_safe
from apps.invoicing.models import Invoice, InvoiceLine, Item, Payment
from apps.ledger.models import Account, JournalLine
from apps.ledger.selectors import account_balances, chain_head
from apps.taxes.models import PphFinalPayment, TaxRate
from apps.tenants.models import Organization

from .statements import build_trial_balance

CSV_DELIMITER = ";"  # what Excel expects in the Indonesian locale, as in the report downloads


@dataclass(frozen=True)
class Column:
    path: str  # model field path; "__" follows relations
    header: str | None = None  # defaults to the field's verbose name


@dataclass(frozen=True)
class Table:
    filename: str
    description: str
    columns: tuple[Column, ...]


def _fields(model: type[Model], path: str) -> list:
    fields = []
    for name in path.split("__"):
        field = model._meta.get_field(name)
        fields.append(field)
        if field.is_relation:
            model = field.related_model
    return fields


def _header(model: type[Model], column: Column) -> str:
    return str(column.header) if column.header else capfirst(str(_fields(model, column.path)[-1].verbose_name))


def _format(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return _("Yes") if value else _("No")
    if isinstance(value, datetime):
        return timezone.localtime(value).isoformat(timespec="seconds")
    if isinstance(value, date):
        return value.isoformat()
    return str(value)  # Decimal: plain "1234.50", whatever the interface language


def _value(obj: Model, fields: list) -> str:
    for field in fields[:-1]:
        obj = getattr(obj, field.name)
        if obj is None:
            return ""
    last = fields[-1]
    if last.choices:
        return str(getattr(obj, f"get_{last.name}_display")())
    return _format(getattr(obj, last.name))


def _rows(queryset: QuerySet, columns: tuple[Column, ...]) -> Iterator[list[str]]:
    model = queryset.model
    paths = [_fields(model, column.path) for column in columns]
    relations = {"__".join(field.name for field in fields[:i]) for fields in paths for i in range(1, len(fields))}
    yield [_header(model, column) for column in columns]
    for obj in queryset.select_related(*sorted(relations)).iterator(chunk_size=2000):
        yield [_value(obj, fields) for fields in paths]


C = Column
ENTRY_LINES = Table(
    "journal_entries.csv",
    gettext_lazy("General journal: one row per line, drafts and posted entries."),
    (
        C("entry__number"), C("entry__date"), C("entry__status"), C("entry__source"),
        C("entry__description", gettext_lazy("Entry description")), C("entry__reference"),
        C("entry__reversal_of__number", gettext_lazy("Reverses entry")), C("entry__posted_at"),
        C("account__code", gettext_lazy("Account code")), C("account__name"),
        C("description", gettext_lazy("Line description")), C("debit"), C("credit"),
        C("entry__posted_by__email", gettext_lazy("Posted by")), C("entry__posting_sequence"), C("entry__entry_hash"),
        C("account_code"), C("account_name"),
    ),
)
TABLES: tuple[tuple[Table, type[Model], str, tuple[str, ...]], ...] = (
    # (table, model, organization lookup, ordering)
    (
        Table("chart_of_accounts.csv", gettext_lazy("Chart of accounts."), (
            C("code"), C("name"), C("category"), C("is_contra"), C("system_role"), C("description"), C("is_active"),
        )),
        Account, "organization", ("code",),
    ),
    (ENTRY_LINES, JournalLine, "entry__organization", ("entry__date", "entry__number", "id")),
    (
        Table("contacts.csv", gettext_lazy("Customers and suppliers."), (
            C("name"), C("kind"), C("npwp"), C("email"), C("phone"), C("address"), C("city"),
            C("payment_terms_days"), C("document_language"), C("notes"), C("is_active"),
        )),
        Contact, "organization", ("name", "id"),
    ),
    (
        Table("items.csv", gettext_lazy("Products and services."), (
            C("code"), C("name"), C("type"), C("unit"), C("sale_price"), C("purchase_price"),
            C("income_account__code", gettext_lazy("Revenue account code")),
            C("expense_account__code", gettext_lazy("Purchase/expense account code")),
            C("tax_rate__name", gettext_lazy("Default tax")), C("is_active"),
        )),
        Item, "organization", ("name", "id"),
    ),
    (
        Table("invoices.csv", gettext_lazy("Sales invoices and purchase bills."), (
            C("id", gettext_lazy("Invoice ID")), C("kind"), C("number"), C("contact__name", gettext_lazy("Contact")),
            C("contact__npwp", gettext_lazy("Contact NPWP")),
            C("contact_name", gettext_lazy("Contact name on the invoice")), C("contact_npwp", gettext_lazy("Contact NPWP on the invoice")),
            C("contact_address"), C("contact_city"), C("seller_name"), C("seller_npwp"),
            C("date"), C("due_date"), C("reference"),
            C("tax_invoice_number"), C("status"), C("subtotal"), C("tax_amount"), C("total"), C("amount_paid"),
            C("journal_entry__number", gettext_lazy("Journal entry")), C("notes"),
        )),
        Invoice, "organization", ("date", "id"),
    ),
    (
        Table("invoice_lines.csv", gettext_lazy("Invoice and bill lines (linked by invoice ID)."), (
            C("invoice__id", gettext_lazy("Invoice ID")), C("invoice__number", gettext_lazy("Invoice number")),
            C("description"), C("item__code", gettext_lazy("Item code")),
            C("account__code", gettext_lazy("Account code")), C("quantity"), C("unit_price"),
            C("discount_percent"), C("tax_rate__name", gettext_lazy("Tax")), C("amount"),
            C("account_code"), C("account_name"),
        )),
        InvoiceLine, "invoice__organization", ("invoice__date", "invoice_id", "id"),
    ),
    (
        Table("payments.csv", gettext_lazy("Receipts and disbursements."), (
            C("number"), C("date"), C("invoice__id", gettext_lazy("Invoice ID")),
            C("invoice__number", gettext_lazy("Invoice number")), C("invoice__contact__name", gettext_lazy("Contact")),
            C("amount"), C("method"), C("cash_account__code", gettext_lazy("Cash/bank account code")),
            C("reference"), C("status"), C("journal_entry__number", gettext_lazy("Journal entry")),
        )),
        Payment, "organization", ("date", "id"),
    ),
    (
        Table("tax_rates.csv", gettext_lazy("PPN rates."), (
            C("name"), C("rate"), C("dpp_numerator"), C("dpp_denominator"), C("is_active"),
        )),
        TaxRate, "organization", ("name",),
    ),
    (
        Table("pph_final_payments.csv", gettext_lazy("MSME PPh Final deposits."), (
            C("year"), C("month"), C("gross_turnover"), C("taxable_turnover"), C("tax_amount"), C("paid_on"),
            C("ntpn"), C("cash_account__code", gettext_lazy("Cash/bank account code")),
            C("journal_entry__number", gettext_lazy("Journal entry")), C("status"),
        )),
        PphFinalPayment, "organization", ("year", "month"),
    ),
)
ORGANIZATION_FIELDS = (
    "name", "legal_form", "npwp", "nib", "address", "city", "phone", "email", "is_pkp", "uses_pph_final", "lock_date",
)
ORGANIZATION_FILE = "organization.csv"
TRIAL_BALANCE_FILE = "trial_balance.csv"
README_FILE = "README.txt"


def _write_csv(archive: zipfile.ZipFile, filename: str, rows: Iterable[list]) -> None:
    with archive.open(filename, "w") as raw, io.TextIOWrapper(raw, encoding="utf-8-sig", newline="") as text:
        writer = csv.writer(text, delimiter=CSV_DELIMITER)
        for row in rows:
            writer.writerow([csv_safe(cell if isinstance(cell, str) else _format(cell)) for cell in row])  # user data


def _organization_rows(organization: Organization) -> Iterator[list[str]]:
    yield [_("Field"), _("Value")]
    for name in ORGANIZATION_FIELDS:
        yield [_header(Organization, Column(name)), _value(organization, _fields(Organization, name))]


def _readme(organization: Organization, exported_at: datetime, exported_by, as_of: date) -> str:
    head = chain_head(organization)
    files = [
        (ORGANIZATION_FILE, _("Business profile.")),
        *((table.filename, str(table.description)) for table, *_rest in TABLES),
        (TRIAL_BALANCE_FILE, _("Trial balance of posted entries as of %(date)s.") % {"date": as_of.isoformat()}),
    ]
    lines = [
        _("Books of %(organization)s") % {"organization": organization.name},
        _("Exported on %(datetime)s by %(user)s.") % {
            "datetime": timezone.localtime(exported_at).isoformat(timespec="seconds"),
            "user": exported_by.email if exported_by else _("the system operator"),
        },
        "",
        _("Files:"),
        *(f"  {name}: {description}" for name, description in sorted(files)),
        "",
        _("CSV files are UTF-8 and separated by semicolons (;). Amounts are in rupiah with a dot as the decimal "
          "separator; dates are YYYY-MM-DD. Account names, descriptions and other business data are exported as "
          "recorded."),
        _("Indonesian taxpayers must keep their books and records for 10 years (UU KUP Art. 28(11))."),
        "",
        _("Ledger chain head: %(head)s") % {"head": head or "-"},
        _("Each posted journal entry carries a SHA-256 hash that includes the previous entry's hash. Keep this "
          "head: if any posted entry is later changed or removed, the chain no longer leads to it "
          "(manage.py verify_ledger --expect-head)."),
    ]
    return "\r\n".join(lines) + "\r\n"


def write_books_archive(organization: Organization, target, *, exported_by=None, as_of: date | None = None) -> None:
    """Write the organization's books as a ZIP archive into the binary file object ``target``."""
    exported_at = timezone.now()
    as_of = as_of or timezone.localdate()
    outermost = not connection.in_atomic_block
    with transaction.atomic(), zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        if outermost:  # one consistent snapshot across all files, even while others keep posting
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        archive.writestr(README_FILE, _readme(organization, exported_at, exported_by, as_of).encode("utf-8-sig"))
        _write_csv(archive, ORGANIZATION_FILE, _organization_rows(organization))
        for table, model, lookup, ordering in TABLES:
            queryset = model.objects.filter(**{lookup: organization}).order_by(*ordering)
            _write_csv(archive, table.filename, _rows(queryset, table.columns))
        trial_balance = build_trial_balance(account_balances(organization, date_to=as_of), as_of)
        _write_csv(archive, TRIAL_BALANCE_FILE, trial_balance.csv_rows())


def archive_filename(organization: Organization, on: date) -> str:
    return f"books-{slugify(organization.name) or organization.pk}-{on.isoformat()}.zip"
