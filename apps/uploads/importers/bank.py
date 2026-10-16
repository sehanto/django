"""A bank statement (mutasi rekening) as the internet banking downloads it, in CSV or XLSX.

Columns are found by their names in the banks' downloads (Tanggal, Keterangan, Debet/Kredit or
one amount with DB/CR, Saldo); amounts like ``1,500,000.00 CR`` or ``-1.500.000`` are read as the
bank wrote them. Lines already uploaded (an overlapping download) are skipped. A new line that has
exactly one posted entry on the bank account with the same amount within five days is reconciled
with it at once; the rest wait on the bank reconciliation page.
"""

from django import forms
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from apps.banking import services as banking
from apps.banking.selectors import bank_accounts, candidates
from apps.core.forms import BaseForm, OrganizationFormMixin

from ..domain import Column, RowOutcome
from .base import Importer, RowError

CREDIT_MARKS = ("CR", "K", "KR", "C")
DEBIT_MARKS = ("DB", "D", "DR", "DEBET", "DEBIT")


class BankStatementOptions(OrganizationFormMixin, BaseForm):
    account = forms.ModelChoiceField(label=gettext_lazy("Bank account"), queryset=None)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["account"].queryset = bank_accounts(self.organization)


class BankStatementImporter(Importer):
    kind = "bank_statement"
    group = "bank"
    title = gettext_lazy("Bank statement")
    description = gettext_lazy(
        "The account statement downloaded from internet banking (CSV or XLSX): date, description, and the amount in "
        "debit and credit columns or in one column marked DB/CR. Lines uploaded before are skipped; each new one is "
        "reconciled with the entry in the books that has the same amount within five days, if there is exactly one."
    )
    options_form = BankStatementOptions
    columns = (
        Column("date", "date", ("tanggal", "tgl", "tanggal transaksi", "tgl transaksi", "transaction date", "posting date",
                                "tanggal posting", "value date"), required=True),
        Column("description", "description", ("keterangan", "uraian", "deskripsi", "uraian transaksi", "remark", "remarks",
                                              "transaction description", "berita", "detail transaksi")),
        Column("reference", "reference", ("referensi", "no referensi", "nomor referensi", "ref", "reference no", "no transaksi")),
        Column("debit", "debit", ("debet", "mutasi debet", "mutasi debit", "keluar", "dana keluar", "withdrawal", "withdrawals",
                                  "jumlah debet")),
        Column("credit", "credit", ("kredit", "mutasi kredit", "masuk", "dana masuk", "deposit", "deposits", "jumlah kredit")),
        Column("amount", "amount", ("jumlah", "nominal", "mutasi", "nilai", "transaction amount")),
        Column("direction", "DB/CR", ("db/cr", "cr/db", "d/k", "dk", "dc", "debit/credit", "tipe", "jenis")),
        Column("balance", "balance", ("saldo", "saldo akhir", "running balance", "ending balance")),
    )
    example = (
        {"date": "02/01/2026", "description": "TRSF E-BANKING CR PT CONTOH PELANGGAN", "credit": "5500000", "balance": "30500000"},
        {"date": "05/01/2026", "description": "BIAYA ADM", "debit": "15000", "balance": "30485000"},
    )

    def row_label(self, row):
        return f"{row.get('date')} {row.get('description')}".strip()

    def prepare(self, context, table, report):
        if not ({"debit", "credit"} & table.found or "amount" in table.found):
            report.problems.append(_("The file has no amount: it needs debit and credit columns, or an amount column."))
        context.account = context.options["account"]
        context.seen = {}

    def _signed(self, row):
        debit = self.amount(row, "debit", _("debit"))
        credit = self.amount(row, "credit", _("credit"))
        if debit or credit:
            if debit and credit:
                raise RowError(_("Enter the amount in either the debit or the credit column."))
            return credit if credit else -debit
        text = row.get("amount").strip().upper()
        mark = row.get("direction").strip().upper()
        for suffix in sorted(CREDIT_MARKS + DEBIT_MARKS, key=len, reverse=True):
            if text.endswith(suffix) and not text[: -len(suffix)].rstrip()[-1:].isalpha():
                mark, text = mark or suffix, text[: -len(suffix)].strip()
                break
        row.values["amount"] = text
        amount = self.amount(row, "amount", _("amount"), allow_negative=True)
        if amount is None or amount == 0:
            raise RowError(_("The line has no amount."))
        if mark in CREDIT_MARKS:
            return abs(amount)
        if mark in DEBIT_MARKS:
            return -abs(amount)
        if mark:
            raise RowError(_("DB/CR: %(value)s is neither.") % {"value": row.get("direction")})
        return amount

    def apply_row(self, context, row):
        on = self.date(row, "date", _("date"), required=True)
        amount = self._signed(row)
        balance = self.amount(row, "balance", _("balance"), allow_negative=True)
        description = row.get("description")
        identity = (on, amount, " ".join(description.lower().split()), balance)
        occurrence = context.seen.get(identity, 0)
        context.seen[identity] = occurrence + 1
        line, new = banking.add_statement_line(
            organization=context.organization, account=context.account, on=on, amount=amount, description=description,
            reference=row.get("reference"), balance=balance, occurrence=occurrence,
        )
        label = f"{on:%d-%m-%Y} {line.description}"
        if not new:
            return RowOutcome.SKIPPED, label, _("Uploaded before.")
        matches = list(candidates(line)[:2])
        if len(matches) == 1:
            banking.match_line(line=line, journal_line=matches[0], matched_by=context.user)
            return RowOutcome.CREATED, label, _("Reconciled with %(entry)s.") % {"entry": matches[0].entry.number}
        return RowOutcome.CREATED, label, _("To reconcile.")
