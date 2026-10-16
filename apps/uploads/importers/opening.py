"""Balances brought over from earlier books, and the fixed asset register that goes with them.

The opening balances file is a trial balance on the last day of the earlier books: one row per
account, debit or credit. What it says about receivables, payables and tracked stock is kept in
the detail the rest of the system works with:

* a row on the receivable (payable) system account names its customer (supplier) and document:
  it becomes an opening balance invoice (``invoicing.services.record_opening_balance``), which is
  collected, paid or written off like any other and shows in the aging;
* a row on the inventory account of a tracked item names the item and quantity: it becomes the
  item's opening stock (``inventory.services.adjust_stock``);
* every other row is a line of one opening balances journal entry.

Those documents and stock post against the opening balance equity account (3-9000 Ekuitas Saldo
Awal), and the entry takes it back, so it is zero once the whole trial balance is in. The file
must balance; the entry is posted on the day of the balances.
"""

from decimal import Decimal

from django.db.models import Q
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from apps.assets import services as asset_services
from apps.assets.forms import FixedAssetForm
from apps.contacts.selectors import contacts_for
from apps.core.forms import BaseForm, BusinessDateField, OrganizationFormMixin
from apps.core.money import ZERO, quantize
from apps.inventory import services as inventory_services
from apps.inventory.selectors import tracked_items
from apps.invoicing import services as invoicing_services
from apps.invoicing.models import InvoiceKind
from apps.ledger.domain import JournalSource, SystemRole
from apps.ledger.models import JournalEntry
from apps.ledger.selectors import accounts_for
from apps.ledger.services import (
    JournalLineInput,
    create_journal_entry,
    get_system_account,
)
from apps.tenants.domain import PlanFeature

from ..domain import Column, RowOutcome, digits
from .base import Importer, RowError, form_errors


def find_contact(organization, text: str, *, kind: str):
    """A customer or supplier by its NPWP (15 or 16 digits) or its exact name."""
    contacts = contacts_for(organization).filter(is_active=True)
    contacts = contacts.filter(kind__in=("customer", "both") if kind == InvoiceKind.SALES else ("supplier", "both"))
    number = digits(text)
    if len(number) in (15, 16) and number == text.replace(".", "").replace("-", "").replace(" ", ""):
        matches = list(contacts.filter(Q(npwp=number) | Q(npwp=number.removeprefix("0")) | Q(npwp="0" + number))[:2])
    else:
        matches = list(contacts.filter(name__iexact=text.strip())[:2])
    if len(matches) == 1:
        return matches[0]
    if matches:
        raise RowError(_("Several contacts are named %(name)s: give the NPWP instead.") % {"name": text})
    what = _("customer") if kind == InvoiceKind.SALES else _("supplier")
    raise RowError(_("There is no %(what)s %(name)s: add it to the contacts first (or upload the contacts).") % {"what": what, "name": text})


class OpeningBalancesOptions(OrganizationFormMixin, BaseForm):
    on = BusinessDateField(
        label=gettext_lazy("Balances as at"),
        help_text=gettext_lazy("The last day in the earlier books, e.g. 31 December 2025: the entry is posted on it."),
    )


class OpeningBalancesImporter(Importer):
    kind = "opening_balances"
    group = "opening"
    title = gettext_lazy("Opening balances")
    description = gettext_lazy(
        "The trial balance of the earlier books on their last day, one row per account. A receivable or payable gives "
        "its customer or supplier (name or NPWP), document number and due date, one row per document still open; the "
        "inventory of a tracked item gives the item code and quantity. Fixed assets are in the balances by their cost "
        "and accumulated depreciation; add them to the register with the fixed assets file."
    )
    options_form = OpeningBalancesOptions
    columns = (
        Column("account", "account code", ("account", "kode akun", "akun", "no akun"), required=True),
        Column("account_name", "account name", ("nama akun",)),
        Column("debit", "debit", ("debet",)),
        Column("credit", "credit", ("kredit",)),
        Column("contact", "contact", ("kontak", "pelanggan", "pemasok", "customer", "supplier", "npwp")),
        Column("document", "document number", ("document", "nomor dokumen", "no faktur", "nomor faktur", "no invoice")),
        Column("document_date", "document date", ("tanggal dokumen", "tanggal faktur")),
        Column("due_date", "due date", ("jatuh tempo", "tanggal jatuh tempo")),
        Column("item", "item code", ("item", "kode barang", "sku")),
        Column("quantity", "quantity", ("kuantitas", "jumlah", "qty")),
    )
    example = (
        {"account": "1-1100", "account_name": "Bank", "debit": "25000000"},
        {"account": "1-1200", "account_name": "Piutang Usaha", "debit": "5500000", "contact": "PT Contoh Pelanggan",
         "document": "INV/2025/0123", "document_date": "15/12/2025", "due_date": "14/01/2026"},
        {"account": "2-1000", "account_name": "Utang Usaha", "credit": "2500000", "contact": "CV Pemasok Bahan",
         "document": "F-778", "document_date": "20/12/2025", "due_date": "19/01/2026"},
        {"account": "3-1000", "account_name": "Modal Pemilik", "credit": "28000000"},
    )

    def row_label(self, row):
        return " ".join(part for part in (row.get("account"), row.get("contact") or row.get("item"), row.get("document")) if part)

    def prepare(self, context, table, report):
        organization = context.organization
        context.lines, context.cleared, context.debits, context.credits = [], ZERO, ZERO, ZERO
        on = context.options["on"]
        later = (
            JournalEntry.objects.for_organization(organization).filter(status=JournalEntry.Status.POSTED, date__lte=on)
            .exclude(source=JournalSource.OPENING).order_by("date").first()
        )
        if later is not None:
            report.problems.append(_(
                "Opening balances come before the first transaction recorded here, but entry %(number)s is dated %(date)s. "
                "Choose an earlier date, or record the earlier transactions as journals."
            ) % {"number": later.number, "date": f"{later.date:%d-%m-%Y}"})
        context.opening = get_system_account(organization, SystemRole.OPENING_BALANCE)
        context.receivable = get_system_account(organization, SystemRole.RECEIVABLE)
        context.payable = get_system_account(organization, SystemRole.PAYABLE)
        context.stocked = {}
        for item in tracked_items(organization):
            context.stocked.setdefault(item.inventory_account_id, {})[item.code.lower()] = item

    def _side(self, row) -> tuple[Decimal, Decimal]:
        debit = self.amount(row, "debit", _("debit")) or ZERO
        credit = self.amount(row, "credit", _("credit")) or ZERO
        if (debit > 0) == (credit > 0):
            raise RowError(_("Enter the balance in either the debit or the credit column."))
        return quantize(debit), quantize(credit)

    def apply_row(self, context, row):
        organization = context.organization
        account = accounts_for(organization, active_only=True).filter(code=row.get("account")).first()
        if account is None:
            raise RowError(_("There is no active account %(code)s.") % {"code": row.get("account")})
        debit, credit = self._side(row)
        context.debits += debit
        context.credits += credit
        if account == context.receivable:
            return self._document(context, row, InvoiceKind.SALES, debit, credit)
        if account == context.payable:
            return self._document(context, row, InvoiceKind.PURCHASE, credit, debit)
        if account.pk in context.stocked:
            return self._stock(context, row, account, debit, credit)
        context.lines.append(JournalLineInput(account, debit=debit, credit=credit, description=row.get("account_name")[:255]))
        return RowOutcome.UPDATED, str(account), ""

    def _document(self, context, row, kind: str, amount: Decimal, wrong_side: Decimal):
        if wrong_side:
            raise RowError(
                _("A customer's credit balance (an advance) goes on a liability account, e.g. Pendapatan Diterima di Muka.")
                if kind == InvoiceKind.SALES else
                _("A debit balance with a supplier (an advance paid) goes on an asset account, e.g. Uang Muka Pembelian.")
            )
        if not row.get("contact"):
            raise RowError(_("Give the customer or supplier of each receivable or payable, one row per open document."))
        contact = find_contact(context.organization, row.get("contact"), kind=kind)
        document_date = self.date(row, "document_date", _("document date"))
        invoice = invoicing_services.record_opening_balance(
            organization=context.organization, kind=kind, contact=contact, on=context.options["on"], amount=amount,
            due_date=self.date(row, "due_date", _("due date")), reference=row.get("document"), created_by=context.user,
            document_date=document_date,
        )
        context.cleared += amount if kind == InvoiceKind.SALES else -amount
        return RowOutcome.CREATED, f"{invoice.number} · {contact.name}", row.get("document")

    def _stock(self, context, row, account, debit: Decimal, credit: Decimal):
        items = context.stocked[account.pk]
        code = row.get("item").lower()
        if not code or code not in items:
            raise RowError(_(
                "Account %(account)s holds tracked stock: give the code of the item and its quantity, one row per item."
            ) % {"account": account})
        if credit:
            raise RowError(_("Stock can't have a credit balance."))
        try:
            quantity = Decimal(row.get("quantity").replace(",", "."))
        except ArithmeticError:
            quantity = ZERO
        if quantity <= 0:
            raise RowError(_("Enter the quantity on hand."))
        item = items[code]
        movement = inventory_services.adjust_stock(
            organization=context.organization, item=item, on=context.options["on"], quantity=quantity,
            counter_account=context.opening, reason="Saldo awal", unit_cost=debit / quantity, created_by=context.user,
        )
        context.cleared += movement.value
        return RowOutcome.CREATED, str(item), f"{quantity.normalize():f} {item.unit}"

    def finish(self, context, table, report):
        if context.debits != context.credits:
            report.problems.append(_(
                "The balances don't balance: debits %(debit)s, credits %(credit)s. A trial balance has as much on each side."
            ) % {"debit": f"{context.debits:,.2f}", "credit": f"{context.credits:,.2f}"})
            return
        lines = list(context.lines)
        if context.cleared > 0:
            lines.append(JournalLineInput(context.opening, debit=context.cleared))
        elif context.cleared < 0:
            lines.append(JournalLineInput(context.opening, credit=-context.cleared))
        if len(lines) >= 2:
            create_journal_entry(
                organization=context.organization, date=context.options["on"], description="Saldo awal",
                source=JournalSource.OPENING, lines=lines, created_by=context.user,
            )


class FixedAssetImporter(Importer):
    kind = "fixed_assets"
    group = "opening"
    feature = PlanFeature.FIXED_ASSETS
    title = gettext_lazy("Fixed assets brought over")
    description = gettext_lazy(
        "The register of assets already in the opening balances (registering posts nothing). Give the accounts by code, "
        "the useful life in months and the month through which the earlier books depreciated them; depreciation goes on "
        "from the next month, without a residual value."
    )
    columns = (
        Column("name", "name", ("nama", "nama aset"), required=True),
        Column("asset_account", "asset account", ("akun aset",), required=True),
        Column("acquisition_date", "acquisition date", ("tanggal perolehan", "tanggal beli"), required=True),
        Column("cost", "cost", ("harga perolehan", "nilai perolehan"), required=True),
        Column("is_depreciable", "depreciable", ("disusutkan",)),
        Column("useful_life_months", "useful life (months)", ("umur manfaat (bulan)", "umur ekonomis (bulan)")),
        Column("accumulated_depreciation_account", "accumulated depreciation account", ("akun akumulasi penyusutan",)),
        Column("depreciation_expense_account", "depreciation expense account", ("akun beban penyusutan",)),
        Column("depreciated_before_until", "depreciated through", ("disusutkan sampai", "penyusutan sampai")),
        Column("description", "description", ("keterangan",)),
    )
    example = (
        {"name": "Mobil boks", "asset_account": "1-2200", "acquisition_date": "05/03/2023", "cost": "180000000",
         "useful_life_months": "96", "accumulated_depreciation_account": "1-2900", "depreciation_expense_account": "6-1600",
         "depreciated_before_until": "31/12/2025"},
        {"name": "Tanah gudang", "asset_account": "1-2000", "acquisition_date": "10/01/2020", "cost": "350000000", "is_depreciable": "no"},
    )

    def row_label(self, row):
        return row.get("name")

    def _account(self, context, row, key: str) -> str:
        code = row.get(key)
        if not code:
            return ""
        account = accounts_for(context.organization).filter(code=code).first()
        return str(account.pk) if account is not None else "0"  # the form says it isn't a choice

    def apply_row(self, context, row):
        depreciable = self.flag(row, "is_depreciable", _("depreciable"), default=True)
        until = self.date(row, "depreciated_before_until", _("depreciated through"))
        data = {
            "name": row.get("name"), "description": row.get("description"), "asset_account": self._account(context, row, "asset_account"),
            "acquisition_date": self.date(row, "acquisition_date", _("acquisition date"), required=True),
            "cost": self.amount(row, "cost", _("cost"), required=True),
            "useful_life_months": row.get("useful_life_months") if depreciable else "",
            "accumulated_depreciation_account": self._account(context, row, "accumulated_depreciation_account") if depreciable else "",
            "depreciation_expense_account": self._account(context, row, "depreciation_expense_account") if depreciable else "",
            "depreciated_before_until": until or "",
        }
        if depreciable:
            data["is_depreciable"] = "on"
        form = FixedAssetForm(data, organization=context.organization)
        if not form.is_valid():
            raise RowError(form_errors(form))
        asset = asset_services.register_asset(organization=context.organization, created_by=context.user, **form.cleaned_data)
        return RowOutcome.CREATED, f"{asset.number} · {asset.name}", ""
