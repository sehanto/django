"""Pure double-entry accounting rules (no database access)."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.db import models

from apps.core.exceptions import PeriodLocked, ValidationFailed
from apps.core.money import ZERO, quantize


class NormalBalance(models.TextChoices):
    DEBIT = "debit", "Debit"
    CREDIT = "credit", "Kredit"


class AccountType(models.TextChoices):
    ASSET = "asset", "Aset"
    LIABILITY = "liability", "Liabilitas"
    EQUITY = "equity", "Ekuitas"
    REVENUE = "revenue", "Pendapatan"
    EXPENSE = "expense", "Beban"

    @property
    def normal_balance(self) -> NormalBalance:
        if self in (AccountType.ASSET, AccountType.EXPENSE):
            return NormalBalance.DEBIT
        return NormalBalance.CREDIT

    @property
    def is_balance_sheet(self) -> bool:
        return self in (AccountType.ASSET, AccountType.LIABILITY, AccountType.EQUITY)


class AccountCategory(models.TextChoices):
    """Statement line groups following the SAK EMKM presentation."""

    CASH_BANK = "cash_bank", "Kas dan Setara Kas"
    RECEIVABLE = "receivable", "Piutang"
    INVENTORY = "inventory", "Persediaan"
    OTHER_CURRENT_ASSET = "other_current_asset", "Aset Lancar Lainnya"
    FIXED_ASSET = "fixed_asset", "Aset Tetap"
    CURRENT_LIABILITY = "current_liability", "Liabilitas Jangka Pendek"
    LONG_TERM_LIABILITY = "long_term_liability", "Liabilitas Jangka Panjang"
    EQUITY = "equity", "Ekuitas"
    REVENUE = "revenue", "Pendapatan Usaha"
    OTHER_INCOME = "other_income", "Pendapatan Lain-lain"
    COST_OF_SALES = "cost_of_sales", "Beban Pokok Penjualan"
    OPERATING_EXPENSE = "operating_expense", "Beban Usaha"
    OTHER_EXPENSE = "other_expense", "Beban Lain-lain"
    INCOME_TAX = "income_tax", "Beban Pajak Penghasilan"

    @property
    def account_type(self) -> AccountType:
        return CATEGORY_TYPES[self]


CATEGORY_TYPES = {
    AccountCategory.CASH_BANK: AccountType.ASSET,
    AccountCategory.RECEIVABLE: AccountType.ASSET,
    AccountCategory.INVENTORY: AccountType.ASSET,
    AccountCategory.OTHER_CURRENT_ASSET: AccountType.ASSET,
    AccountCategory.FIXED_ASSET: AccountType.ASSET,
    AccountCategory.CURRENT_LIABILITY: AccountType.LIABILITY,
    AccountCategory.LONG_TERM_LIABILITY: AccountType.LIABILITY,
    AccountCategory.EQUITY: AccountType.EQUITY,
    AccountCategory.REVENUE: AccountType.REVENUE,
    AccountCategory.OTHER_INCOME: AccountType.REVENUE,
    AccountCategory.COST_OF_SALES: AccountType.EXPENSE,
    AccountCategory.OPERATING_EXPENSE: AccountType.EXPENSE,
    AccountCategory.OTHER_EXPENSE: AccountType.EXPENSE,
    AccountCategory.INCOME_TAX: AccountType.EXPENSE,
}


def categories_of(account_type: AccountType) -> list[str]:
    return [c.value for c, t in CATEGORY_TYPES.items() if t == account_type]


class SystemRole(models.TextChoices):
    """Accounts that automated postings (invoices, payments, taxes) rely on."""

    RECEIVABLE = "receivable", "Piutang Usaha"
    PAYABLE = "payable", "Utang Usaha"
    OUTPUT_VAT = "output_vat", "PPN Keluaran"
    INPUT_VAT = "input_vat", "PPN Masukan"
    SALES_REVENUE = "sales_revenue", "Pendapatan Penjualan"
    PURCHASE_EXPENSE = "purchase_expense", "Pembelian / Beban Pokok"
    RETAINED_EARNINGS = "retained_earnings", "Saldo Laba"
    PPH_FINAL_EXPENSE = "pph_final_expense", "Beban PPh Final"
    PPH_FINAL_PAYABLE = "pph_final_payable", "Utang PPh Final"


def normal_balance_for(category: str, is_contra: bool) -> NormalBalance:
    normal = AccountCategory(category).account_type.normal_balance
    if not is_contra:
        return normal
    return NormalBalance.CREDIT if normal == NormalBalance.DEBIT else NormalBalance.DEBIT


def signed_balance(debit: Decimal, credit: Decimal, normal: NormalBalance) -> Decimal:
    """Balance expressed in the account's natural direction (positive = normal)."""
    return quantize(debit - credit) if normal == NormalBalance.DEBIT else quantize(credit - debit)


@dataclass(frozen=True)
class LineAmount:
    debit: Decimal = ZERO
    credit: Decimal = ZERO


def validate_lines(lines: Iterable[LineAmount]) -> tuple[Decimal, Decimal]:
    """Enforce the double-entry invariants; return (total_debit, total_credit)."""
    lines = list(lines)
    if len(lines) < 2:
        raise ValidationFailed("Jurnal minimal terdiri dari dua baris.")
    total_debit = total_credit = ZERO
    for line in lines:
        debit, credit = quantize(line.debit), quantize(line.credit)
        if debit < 0 or credit < 0:
            raise ValidationFailed("Nilai debit/kredit tidak boleh negatif.")
        if (debit > 0) == (credit > 0):
            raise ValidationFailed("Setiap baris jurnal harus berisi debit atau kredit saja.")
        total_debit += debit
        total_credit += credit
    if total_debit != total_credit:
        raise ValidationFailed(
            f"Jurnal tidak seimbang: total debit {total_debit} ≠ total kredit {total_credit}."
        )
    return total_debit, total_credit


def ensure_period_open(entry_date: date, lock_date: date | None) -> None:
    if lock_date is not None and entry_date <= lock_date:
        raise PeriodLocked(
            f"Periode sampai {lock_date:%d-%m-%Y} sudah dikunci. Gunakan tanggal setelahnya."
        )
