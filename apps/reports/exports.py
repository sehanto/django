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

from apps.assets.models import AssetRevision, DepreciationPosting, FixedAsset
from apps.contacts.models import Contact
from apps.inventory.models import StockMovement
from apps.core.sanitizers import csv_safe
from apps.invoicing.models import Invoice, InvoiceLine, Item, Payment, ReturnLine, ReturnNote
from apps.ledger.models import Account, JournalLine
from apps.ledger.selectors import account_balances, chain_head
from apps.taxes.models import ImportDeclaration, PphFinalAccrual, PphFinalPayment, SelfDeposit, TaxRate, WithholdingDeposit
from apps.payroll.models import Employee, PayrollRun, Payslip, PriorIncome
from apps.tenants.models import BusinessPlace, Organization

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
            C("name"), C("kind"), C("npwp"), C("nitku"), C("id_type"), C("id_number"), C("country"), C("foreign_tin"),
            C("treaty_document"), C("email"), C("phone"), C("address"), C("city"),
            C("payment_terms_days"), C("document_language"), C("vat_collector"), C("notes"), C("is_active"),
        )),
        Contact, "organization", ("name", "id"),
    ),
    (
        Table("items.csv", gettext_lazy("Products and services."), (
            C("code"), C("name"), C("type"), C("unit"), C("sale_price"), C("purchase_price"),
            C("income_account__code", gettext_lazy("Revenue account code")),
            C("expense_account__code", gettext_lazy("Purchase/expense account code")),
            C("tax_rate__name", gettext_lazy("Default tax")), C("track_inventory"),
            C("inventory_account__code", gettext_lazy("Inventory account code")),
            C("cogs_account__code", gettext_lazy("Cost of goods sold account code")), C("is_active"),
        )),
        Item, "organization", ("name", "id"),
    ),
    (
        Table("stock_movements.csv", gettext_lazy("The stock card: every movement of tracked items, in posting order."), (
            C("item__code", gettext_lazy("Item code")), C("item__name", gettext_lazy("Item")), C("date"), C("kind"),
            C("quantity"), C("value"), C("cost_formula"), C("invoice_line__invoice__number", gettext_lazy("Invoice")),
            C("journal_entry__number", gettext_lazy("Journal entry")), C("reason"),
        )),
        StockMovement, "organization", ("id",),
    ),
    (
        Table("invoices.csv", gettext_lazy("Sales invoices and purchase bills."), (
            C("id", gettext_lazy("Invoice ID")), C("kind"), C("number"), C("contact__name", gettext_lazy("Contact")),
            C("contact__npwp", gettext_lazy("Contact NPWP")),
            C("contact_name", gettext_lazy("Contact name on the invoice")), C("contact_npwp", gettext_lazy("Contact NPWP on the invoice")),
            C("contact_nitku"), C("contact_id_type"), C("contact_id_number"), C("contact_country"),
            C("contact_address"), C("contact_city"), C("buyer_vat_collector"), C("seller_name"), C("seller_npwp"), C("seller_nitku"),
            C("date"), C("due_date"), C("reference"),
            C("tax_invoice_number"), C("delivery_kind"), C("business_place__nitku", gettext_lazy("Place of business NITKU")),
            C("vat_facility"), C("facility_info"), C("facility_stamp"), C("special_document"), C("special_document_number"),
            C("special_document_date"),
            C("supporting_document"), C("supporting_document_date"),
            C("status"), C("subtotal"), C("tax_amount"), C("vat_not_collected"), C("luxury_tax_amount"), C("total"), C("amount_paid"),
            C("amount_credited"), C("journal_entry__number", gettext_lazy("Journal entry")), C("notes"),
        )),
        Invoice, "organization", ("date", "id"),
    ),
    (
        Table("invoice_lines.csv", gettext_lazy("Invoice and bill lines (linked by invoice ID)."), (
            C("invoice__id", gettext_lazy("Invoice ID")), C("invoice__number", gettext_lazy("Invoice number")),
            C("description"), C("item_code", gettext_lazy("Item code")),
            C("account__code", gettext_lazy("Account code")), C("quantity"), C("unit_price"),
            C("discount_percent"), C("tax_rate__name", gettext_lazy("Tax")), C("amount"),
            C("account_code"), C("account_name"),
            C("tax_name"), C("tax_rate_percent"), C("tax_dpp_numerator"), C("tax_dpp_denominator"),
            C("tax_specific_amount"), C("tax_luxury_rate"),
        )),
        InvoiceLine, "invoice__organization", ("invoice__date", "invoice_id", "id"),
    ),
    (
        Table("returns.csv", gettext_lazy("Sales and purchase returns (nota retur)."), (
            C("kind"), C("number"), C("invoice__number", gettext_lazy("Invoice number")), C("date"), C("reason"),
            C("subtotal"), C("tax_amount"), C("vat_not_collected"), C("luxury_tax_amount"), C("total"), C("credited"), C("refunded"),
            C("vat_withheld"), C("income_tax_kind"), C("income_tax_withheld"),
            C("income_tax_account__code", gettext_lazy("Prepaid tax account code")),
            C("cash_account__code", gettext_lazy("Refund cash/bank account code")),
            C("journal_entry__number", gettext_lazy("Journal entry")),
        )),
        ReturnNote, "organization", ("date", "id"),
    ),
    (
        Table("return_lines.csv", gettext_lazy("Return lines (linked by return number)."), (
            C("return_note__number", gettext_lazy("Return number")), C("invoice_line__description", gettext_lazy("Item description")),
            C("invoice_line__item_code", gettext_lazy("Item code")), C("quantity"), C("amount"),
        )),
        ReturnLine, "return_note__organization", ("return_note__date", "return_note_id", "id"),
    ),
    (
        Table("payments.csv", gettext_lazy("Receipts and disbursements."), (
            C("number"), C("date"), C("invoice__id", gettext_lazy("Invoice ID")),
            C("invoice__number", gettext_lazy("Invoice number")), C("invoice__contact__name", gettext_lazy("Contact")),
            C("amount"), C("method"), C("cash_account__code", gettext_lazy("Cash/bank account code")),
            C("vat_withheld"), C("income_tax_kind"), C("income_tax_withheld"),
            C("income_tax_account__code", gettext_lazy("Prepaid tax account code")), C("income_tax_base"), C("income_tax_rate"),
            C("income_tax_object"), C("withholding_slip"), C("reference"), C("status"), C("journal_entry__number", gettext_lazy("Journal entry")),
        )),
        Payment, "organization", ("date", "id"),
    ),
    (
        Table("tax_rates.csv", gettext_lazy("PPN rates."), (
            C("name"), C("rate"), C("dpp_numerator"), C("dpp_denominator"), C("is_specific_amount"), C("luxury_rate"), C("is_active"),
        )),
        TaxRate, "organization", ("name",),
    ),
    (
        Table("business_places.csv", gettext_lazy("Places of business (branches) and their NITKU."), (
            C("name"), C("nitku"), C("address"), C("city"), C("is_active"),
        )),
        BusinessPlace, "organization", ("name", "id"),
    ),
    (
        Table("fixed_assets.csv", gettext_lazy("The fixed asset register."), (
            C("number"), C("name"), C("description"), C("asset_account__code", gettext_lazy("Fixed asset account code")),
            C("acquisition_date"), C("cost"), C("is_depreciable"), C("residual_value"), C("useful_life_months"), C("method"),
            C("accumulated_depreciation_account__code", gettext_lazy("Accumulated depreciation account code")),
            C("depreciation_expense_account__code", gettext_lazy("Depreciation expense account code")),
            C("depreciated_before_until"), C("status"), C("disposed_on"), C("disposal_proceeds"),
            C("disposal_entry__number", gettext_lazy("Disposal journal entry")),
            C("disposal_invoice__number", gettext_lazy("Disposal sales invoice")),
        )),
        FixedAsset, "organization", ("number",),
    ),
    (
        Table("asset_revisions.csv", gettext_lazy("Changes in the useful life, residual value and depreciation method of fixed assets."), (
            C("asset__number", gettext_lazy("Fixed asset")), C("effective_from"), C("previous_useful_life_months"),
            C("useful_life_months"), C("previous_residual_value"), C("residual_value"), C("previous_method"), C("method"),
            C("reason"),
        )),
        AssetRevision, "organization", ("asset__number", "effective_from", "pk"),
    ),
    (
        Table("depreciation.csv", gettext_lazy("Depreciation posted per asset and month."), (
            C("asset__number", gettext_lazy("Fixed asset")), C("year"), C("month"), C("amount"),
            C("journal_entry__number", gettext_lazy("Journal entry")),
        )),
        DepreciationPosting, "organization", ("asset__number", "year", "month"),
    ),
    (
        Table("pph_final_expenses.csv", gettext_lazy("MSME PPh Final expenses, recognised at the end of each month."), (
            C("year"), C("month"), C("gross_turnover"), C("taxable_turnover"), C("tax_amount"),
            C("journal_entry__number", gettext_lazy("Journal entry")), C("status"),
        )),
        PphFinalAccrual, "organization", ("year", "month"),
    ),
    (
        Table("pph_final_payments.csv", gettext_lazy("MSME PPh Final deposits."), (
            C("year"), C("month"), C("gross_turnover"), C("taxable_turnover"), C("tax_amount"), C("withheld"), C("paid_on"),
            C("ntpn"), C("cash_account__code", gettext_lazy("Cash/bank account code")),
            C("journal_entry__number", gettext_lazy("Journal entry")), C("status"),
        )),
        PphFinalPayment, "organization", ("year", "month"),
    ),
    (
        Table("withholding_deposits.csv", gettext_lazy("Deposits of PPh 23 and 4(2) withheld from suppliers."), (
            C("kind"), C("year"), C("month"), C("amount"), C("paid_on"), C("ntpn"),
            C("cash_account__code", gettext_lazy("Cash/bank account code")),
            C("journal_entry__number", gettext_lazy("Journal entry")), C("status"),
        )),
        WithholdingDeposit, "organization", ("year", "month", "kind"),
    ),
    (
        Table("import_declarations.csv", gettext_lazy("Imports (PIB) and the taxes paid at customs."), (
            C("number"), C("date"), C("supplier_name"), C("bill__number", gettext_lazy("Bill")), C("customs_value"), C("import_duty"),
            C("vat"), C("luxury_tax"), C("pph22"), C("cost_account__code", gettext_lazy("Cost account code")),
            C("prepaid_tax_account__code", gettext_lazy("Prepaid tax account code")),
            C("cash_account__code", gettext_lazy("Cash/bank account code")), C("ntpn"),
            C("journal_entry__number", gettext_lazy("Journal entry")), C("status"),
        )),
        ImportDeclaration, "organization", ("date", "id"),
    ),
    (
        Table("self_deposits.csv", gettext_lazy("Final income tax deposited on your own income."), (
            C("tax_object"), C("year"), C("month"), C("tax_base"), C("rate"), C("amount"), C("invoice__number", gettext_lazy("Invoice")),
            C("document_number"), C("document_date"), C("paid_on"), C("ntpn"), C("cash_account__code", gettext_lazy("Cash/bank account code")),
            C("expense_entry__number", gettext_lazy("Expense entry")), C("payment_entry__number", gettext_lazy("Payment entry")),
            C("slip_number"), C("status"),
        )),
        SelfDeposit, "organization", ("year", "month", "id"),
    ),
    (
        Table("employees.csv", gettext_lazy("Employees."), (
            C("name"), C("kind"), C("pay_basis"), C("nationality"), C("nik"), C("passport_number"), C("npwp"), C("is_resident"),
            C("resident_since"), C("leaves_indonesia"), C("treaty_rate"), C("treaty_document"), C("tax_borne_by_employer"), C("zakat"),
            C("gender"), C("address"), C("email"), C("position"), C("ptkp_status"),
            C("start_date"), C("end_date"), C("base_salary"), C("fixed_allowance"), C("bpjs_employment"), C("bpjs_health"), C("is_active"),
        )),
        Employee, "organization", ("name", "id"),
    ),
    (
        Table("payroll_runs.csv", gettext_lazy("Monthly payroll runs."), (
            C("year"), C("month"), C("status"), C("gross"), C("employer_contributions"), C("employee_contributions"), C("pph21"),
            C("pph26"), C("zakat"), C("net_pay"), C("journal_entry__number", gettext_lazy("Journal entry")), C("paid_on"), C("bpjs_paid_on"),
        )),
        PayrollRun, "organization", ("year", "month", "id"),
    ),
    (
        Table("payslips.csv", gettext_lazy("Payslips, with their PPh 21 or PPh 26."), (
            C("run__year", gettext_lazy("Year")), C("run__month", gettext_lazy("Month")), C("employee__name", gettext_lazy("Employee")),
            C("employee__nik", gettext_lazy("NIK")), C("ptkp_status"), C("base_salary"), C("allowances"), C("bonus"), C("tax_allowance"),
            C("benefits_in_kind"), C("severance"), C("severance_tax"), C("zakat"), C("jkk"), C("jkm"),
            C("jht_employer"), C("jp_employer"), C("health_employer"), C("jht_employee"), C("jp_employee"), C("health_employee"),
            C("taxable_gross"), C("ter_category"), C("ter_rate"), C("is_final_period"), C("tax_scheme"), C("days_worked"), C("annualized"), C("pph26_rate"),
            C("without_tin"), C("pph21"), C("net_pay"), C("withholding_slip"),
        )),
        Payslip, "run__organization", ("run__year", "run__month", "employee__name", "id"),
    ),
    (
        Table("prior_incomes.csv", gettext_lazy("Income from employees' previous employers (their 1721-A1)."), (
            C("employee__name", gettext_lazy("Employee")), C("year"), C("employer_name"), C("employer_npwp"), C("slip_number"),
            C("first_month"), C("last_month"), C("net_income"), C("pph21"),
        )),
        PriorIncome, "organization", ("year", "employee__name", "id"),
    ),
)
ORGANIZATION_FIELDS = (
    "name", "legal_form", "npwp", "nib", "address", "city", "phone", "email", "is_pkp", "is_retailer", "uses_pph_final",
    "inventory_cost_formula", "depreciation_method", "additional_notes", "lock_date",
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
