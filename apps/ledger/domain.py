"""Pure double-entry accounting rules (no database access)."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.exceptions import PeriodLocked, ValidationFailed
from apps.core.formatting import localized_rupiah
from apps.core.money import ZERO, quantize


class NormalBalance(models.TextChoices):
    DEBIT = "debit", _("Debit")
    CREDIT = "credit", _("Credit")


class AccountType(models.TextChoices):
    ASSET = "asset", _("Asset")
    LIABILITY = "liability", _("Liability")
    EQUITY = "equity", _("Equity")
    REVENUE = "revenue", _("Revenue")
    EXPENSE = "expense", _("Expense")

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

    CASH_BANK = "cash_bank", _("Cash and cash equivalents")
    RECEIVABLE = "receivable", _("Receivables")
    INVENTORY = "inventory", _("Inventory")
    OTHER_CURRENT_ASSET = "other_current_asset", _("Other current assets")
    FIXED_ASSET = "fixed_asset", _("Fixed assets")
    CURRENT_LIABILITY = "current_liability", _("Current liabilities")
    LONG_TERM_LIABILITY = "long_term_liability", _("Long-term liabilities")
    EQUITY = "equity", _("Equity")
    REVENUE = "revenue", _("Operating revenue")
    OTHER_INCOME = "other_income", _("Other income")
    COST_OF_SALES = "cost_of_sales", _("Cost of sales")
    OPERATING_EXPENSE = "operating_expense", _("Operating expenses")
    OTHER_EXPENSE = "other_expense", _("Other expenses")
    INCOME_TAX = "income_tax", _("Income tax expense")

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

    RECEIVABLE = "receivable", _("Accounts receivable")
    PAYABLE = "payable", _("Accounts payable")
    OUTPUT_VAT = "output_vat", _("Output VAT (PPN Keluaran)")
    INPUT_VAT = "input_vat", _("Input VAT (PPN Masukan)")
    SALES_REVENUE = "sales_revenue", _("Sales revenue")
    PURCHASE_EXPENSE = "purchase_expense", _("Purchases / cost of sales")
    RETAINED_EARNINGS = "retained_earnings", _("Retained earnings")
    PPH_FINAL_EXPENSE = "pph_final_expense", _("Final income tax expense")
    PPH_FINAL_PAYABLE = "pph_final_payable", _("Final income tax payable")


class JournalStatus(models.TextChoices):
    DRAFT = "draft", _("Draft")
    POSTED = "posted", _("Posted")


class JournalSource(models.TextChoices):
    MANUAL = "manual", _("General journal")
    SALES_INVOICE = "sales_invoice", _("Sales invoice")
    PURCHASE_INVOICE = "purchase_invoice", _("Purchase bill")
    PAYMENT = "payment", _("Payment")
    TAX = "tax", _("Tax")
    FIXED_ASSET = "fixed_asset", _("Fixed asset")
    REVERSAL = "reversal", _("Reversal")


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
        raise ValidationFailed(_("A journal entry needs at least two lines."))
    total_debit = total_credit = ZERO
    for line in lines:
        debit, credit = quantize(line.debit), quantize(line.credit)
        if debit < 0 or credit < 0:
            raise ValidationFailed(_("Debit and credit amounts cannot be negative."))
        if (debit > 0) == (credit > 0):
            raise ValidationFailed(_("Each journal line must have either a debit or a credit, not both."))
        total_debit += debit
        total_credit += credit
    if total_debit != total_credit:
        raise ValidationFailed(
            _("The entry is not balanced: total debit %(debit)s ≠ total credit %(credit)s.")
            % {"debit": localized_rupiah(total_debit), "credit": localized_rupiah(total_credit)}
        )
    return total_debit, total_credit


def ensure_period_open(entry_date: date, lock_date: date | None) -> None:
    if lock_date is not None and entry_date <= lock_date:
        raise PeriodLocked(
            _("The period up to %(date)s is locked. Use a later date.") % {"date": f"{lock_date:%d-%m-%Y}"}
        )
