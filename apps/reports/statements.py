"""Financial statements per SAK EMKM, built from account balances.

Builders are pure transformations of ``AccountBalance`` lists, so they can be
tested without the HTTP layer and reused by exports or APIs.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from django.utils.translation import gettext_lazy as _

from apps.core.money import ZERO
from apps.ledger.domain import AccountCategory as C
from apps.ledger.domain import AccountType, NormalBalance
from apps.ledger.selectors import AccountBalance


@dataclass
class StatementRow:
    code: str
    name: str
    amount: Decimal
    previous: Decimal = ZERO  # the comparative period (SAK EMKM presents the prior period alongside)


@dataclass
class Section:
    title: str
    rows: list[StatementRow] = field(default_factory=list)
    note: int | None = None  # its note in the notes to the financial statements (SAK EMKM 6.4)

    @property
    def total(self) -> Decimal:
        return sum((r.amount for r in self.rows), ZERO)

    @property
    def previous_total(self) -> Decimal:
        return sum((r.previous for r in self.rows), ZERO)


def _signed(balance: AccountBalance, sign_type: AccountType) -> Decimal:
    return balance.net_debit if sign_type.normal_balance == NormalBalance.DEBIT else -balance.net_debit


def _section(
    title: str, balances: Iterable[AccountBalance], categories: set[str], sign_type: AccountType,
    previous: Iterable[AccountBalance] = (),
) -> Section:
    """Rows are signed by the *section's* normal balance, so contra accounts reduce it.

    An account appears when it has a balance in either period.
    """
    current = {b.account.pk: b for b in balances if b.account.category in categories}
    earlier = {b.account.pk: b for b in previous if b.account.category in categories}
    accounts = {b.account.pk: b.account for b in (*earlier.values(), *current.values())}
    section = Section(title)
    for pk, account in sorted(accounts.items(), key=lambda item: item[1].code):
        amount = _signed(current[pk], sign_type) if pk in current else ZERO
        before = _signed(earlier[pk], sign_type) if pk in earlier else ZERO
        if amount or before:
            section.rows.append(StatementRow(account.code, account.name, amount, before))
    return section


def _total(section: Section, previous: bool) -> Decimal:
    return section.previous_total if previous else section.total


def _period_label(start: date, end: date) -> str:
    return _("%(start)s to %(end)s") % {"start": f"{start:%d-%m-%Y}", "end": f"{end:%d-%m-%Y}"}


# --- Income statement --------------------------------------------------------


@dataclass
class IncomeStatement:
    date_from: date
    date_to: date
    revenue: Section
    cost_of_sales: Section
    operating_expenses: Section
    other_income: Section
    other_expenses: Section
    finance_costs: Section
    income_tax: Section
    previous_from: date | None = None
    previous_to: date | None = None
    comparative_restated: bool = False  # a later prior-period correction changed it (SAK EMKM 7.16)

    @property
    def has_comparative(self) -> bool:
        return self.previous_from is not None

    def _gross_profit(self, previous=False) -> Decimal:
        return _total(self.revenue, previous) - _total(self.cost_of_sales, previous)

    def _operating_profit(self, previous=False) -> Decimal:
        return self._gross_profit(previous) - _total(self.operating_expenses, previous)

    def _profit_before_tax(self, previous=False) -> Decimal:
        return (self._operating_profit(previous) + _total(self.other_income, previous) - _total(self.other_expenses, previous)
                - _total(self.finance_costs, previous))

    def _net_profit(self, previous=False) -> Decimal:
        return self._profit_before_tax(previous) - _total(self.income_tax, previous)

    gross_profit = property(_gross_profit)
    operating_profit = property(_operating_profit)
    profit_before_tax = property(_profit_before_tax)
    net_profit = property(_net_profit)
    previous_gross_profit = property(lambda self: self._gross_profit(True))
    previous_operating_profit = property(lambda self: self._operating_profit(True))
    previous_profit_before_tax = property(lambda self: self._profit_before_tax(True))
    previous_net_profit = property(lambda self: self._net_profit(True))

    def csv_rows(self):
        columns = [_period_label(self.date_from, self.date_to)]
        if self.has_comparative:
            columns.append(_period_label(self.previous_from, self.previous_to) + (f" ({_('restated')})" if self.comparative_restated else ""))
        yield [_("Income statement"), _("Amounts in Rupiah"), *columns]
        for section, subtotal, previous in [
            (self.revenue, None, None),
            (self.cost_of_sales, (_("Gross profit"), self.gross_profit), self.previous_gross_profit),
            (self.operating_expenses, (_("Operating profit"), self.operating_profit), self.previous_operating_profit),
            (self.other_income, None, None),
            (self.other_expenses, None, None),
            (self.finance_costs, (_("Profit before tax"), self.profit_before_tax), self.previous_profit_before_tax),
            (self.income_tax, (_("Net profit"), self.net_profit), self.previous_net_profit),
        ]:
            yield from _section_csv(section, self.has_comparative)
            if subtotal:
                yield ["", subtotal[0], subtotal[1], *([previous] if self.has_comparative else [])]


def build_income_statement(
    balances: list[AccountBalance], date_from: date, date_to: date, *,
    previous_balances: list[AccountBalance] | None = None, previous_from: date | None = None, previous_to: date | None = None,
) -> IncomeStatement:
    rev, exp = AccountType.REVENUE, AccountType.EXPENSE
    earlier = previous_balances or ()

    def section(title, category, sign_type):
        return _section(title, balances, {category}, sign_type, earlier)

    return IncomeStatement(
        date_from=date_from,
        date_to=date_to,
        revenue=section(_("Operating revenue"), C.REVENUE, rev),
        cost_of_sales=section(_("Cost of sales"), C.COST_OF_SALES, exp),
        operating_expenses=section(_("Operating expenses"), C.OPERATING_EXPENSE, exp),
        other_income=section(_("Other income"), C.OTHER_INCOME, rev),
        other_expenses=section(_("Other expenses"), C.OTHER_EXPENSE, exp),
        finance_costs=section(_("Finance costs"), C.FINANCE_COST, exp),
        income_tax=section(_("Income tax expense"), C.INCOME_TAX, exp),
        previous_from=previous_from if previous_balances is not None else None,
        previous_to=previous_to if previous_balances is not None else None,
    )


def net_income(balances: Iterable[AccountBalance]) -> Decimal:
    """Revenue minus expenses = sum of credit-minus-debit on P&L accounts."""
    return sum(
        (-b.net_debit for b in balances if not b.account.account_type.is_balance_sheet),
        ZERO,
    )


# --- Balance sheet -----------------------------------------------------------


@dataclass
class BalanceSheet:
    as_of: date
    current_assets: Section
    fixed_assets: Section
    intangible_assets: Section
    investments: Section  # in joint ventures, at cost (SAK EMKM 10.6: a group of assets)
    current_liabilities: Section
    long_term_liabilities: Section
    equity: Section
    prior_years_earnings: Decimal
    current_year_earnings: Decimal
    previous_as_of: date | None = None
    previous_prior_years_earnings: Decimal = ZERO
    previous_current_year_earnings: Decimal = ZERO
    comparative_restated: bool = False  # a later prior-period correction changed it (SAK EMKM 7.16)

    @property
    def has_comparative(self) -> bool:
        return self.previous_as_of is not None

    def _total_assets(self, previous=False) -> Decimal:
        return sum(
            (_total(s, previous) for s in (self.current_assets, self.fixed_assets, self.intangible_assets, self.investments)), ZERO
        )

    def _total_liabilities(self, previous=False) -> Decimal:
        return _total(self.current_liabilities, previous) + _total(self.long_term_liabilities, previous)

    def _total_equity(self, previous=False) -> Decimal:
        if previous:
            return self.equity.previous_total + self.previous_prior_years_earnings + self.previous_current_year_earnings
        return self.equity.total + self.prior_years_earnings + self.current_year_earnings

    def _total_liabilities_and_equity(self, previous=False) -> Decimal:
        return self._total_liabilities(previous) + self._total_equity(previous)

    total_assets = property(_total_assets)
    total_liabilities = property(_total_liabilities)
    total_equity = property(_total_equity)
    total_liabilities_and_equity = property(_total_liabilities_and_equity)
    previous_total_assets = property(lambda self: self._total_assets(True))
    previous_total_liabilities = property(lambda self: self._total_liabilities(True))
    previous_total_equity = property(lambda self: self._total_equity(True))
    previous_total_liabilities_and_equity = property(lambda self: self._total_liabilities_and_equity(True))

    @property
    def is_balanced(self) -> bool:
        return all(self._total_assets(p) == self._total_liabilities_and_equity(p) for p in (False, True))

    def csv_rows(self):
        compare = self.has_comparative
        restated = f" ({_('restated')})" if self.comparative_restated else ""
        columns = [f"{self.as_of:%d-%m-%Y}", *([f"{self.previous_as_of:%d-%m-%Y}{restated}"] if compare else [])]

        def row(label, current, previous):
            return ["", label, current, *([previous] if compare else [])]

        yield [_("Statement of financial position"), _("Amounts in Rupiah"), *columns]
        yield from _section_csv(self.current_assets, compare)
        yield from _section_csv(self.fixed_assets, compare)
        yield from _section_csv(self.intangible_assets, compare)
        yield from _section_csv(self.investments, compare)
        yield row(_("TOTAL ASSETS"), self.total_assets, self.previous_total_assets)
        yield from _section_csv(self.current_liabilities, compare)
        yield from _section_csv(self.long_term_liabilities, compare)
        yield row(_("Total liabilities"), self.total_liabilities, self.previous_total_liabilities)
        yield from _section_csv(self.equity, compare)
        yield row(_("Prior years' retained earnings (not yet closed)"), self.prior_years_earnings, self.previous_prior_years_earnings)
        yield row(_("Current year profit"), self.current_year_earnings, self.previous_current_year_earnings)
        yield row(_("TOTAL LIABILITIES AND EQUITY"), self.total_liabilities_and_equity, self.previous_total_liabilities_and_equity)


CURRENT_ASSET_CATEGORIES = {C.CASH_BANK, C.RECEIVABLE, C.INVENTORY, C.OTHER_CURRENT_ASSET}


def build_balance_sheet(
    balances_to_date: list[AccountBalance], balances_before_year: list[AccountBalance], as_of: date, *,
    previous_balances_to_date: list[AccountBalance] | None = None,
    previous_balances_before_year: list[AccountBalance] | None = None,
    previous_as_of: date | None = None,
) -> BalanceSheet:
    """``balances_before_year`` are cumulative balances up to the day before the fiscal year starts;
    the ``previous_*`` arguments are the same for the comparative date."""
    asset, liability, equity = AccountType.ASSET, AccountType.LIABILITY, AccountType.EQUITY
    compare = previous_balances_to_date is not None
    earlier = previous_balances_to_date or ()

    def section(title, categories, sign_type):
        return _section(title, balances_to_date, categories, sign_type, earlier)

    prior = net_income(balances_before_year)
    previous_prior = net_income(previous_balances_before_year or ())
    return BalanceSheet(
        as_of=as_of,
        current_assets=section(_("Current assets"), CURRENT_ASSET_CATEGORIES, asset),
        fixed_assets=section(_("Fixed assets"), {C.FIXED_ASSET}, asset),
        intangible_assets=section(_("Intangible assets"), {C.INTANGIBLE_ASSET}, asset),
        investments=section(_("Investments in joint ventures"), {C.INVESTMENT}, asset),
        current_liabilities=section(_("Current liabilities"), {C.CURRENT_LIABILITY}, liability),
        long_term_liabilities=section(_("Long-term liabilities"), {C.LONG_TERM_LIABILITY}, liability),
        equity=section(_("Capital"), {C.EQUITY}, equity),
        prior_years_earnings=prior,
        current_year_earnings=net_income(balances_to_date) - prior,
        previous_as_of=previous_as_of if compare else None,
        previous_prior_years_earnings=previous_prior if compare else ZERO,
        previous_current_year_earnings=net_income(earlier) - previous_prior if compare else ZERO,
    )


# --- Notes to the financial statements ----------------------------------------


@dataclass(frozen=True)
class ItemNote:
    section: Section
    is_balance_sheet: bool  # compares two dates; otherwise two periods


@dataclass
class FinancialNotes:
    """Catatan atas Laporan Keuangan: with the two statements, the minimum SAK EMKM set.

    The policy wording lives in the template; this holds the figures the notes explain.
    """

    organization: object
    balance_sheet: BalanceSheet
    income_statement: IncomeStatement
    estimate_changes: list = field(default_factory=list)  # fixed asset revisions effective in the period
    corrections: list = field(default_factory=list)  # prior-period corrections posted in the period (7.16)

    @property
    def item_notes(self) -> list[ItemNote]:
        """One note per statement line group that has a balance in either period."""
        return item_notes(self.balance_sheet, self.income_statement)

    def _after_items(self) -> list[str]:
        return [*(["estimates"] if self.estimate_changes else []), *(["corrections"] if self.corrections else []), "additional"]

    def _number(self, name: str) -> int:
        return FIRST_ITEM_NOTE + len(self.item_notes) + self._after_items().index(name)

    estimates_note = property(lambda self: self._number("estimates"))
    corrections_note = property(lambda self: self._number("corrections"))
    additional_note = property(lambda self: self._number("additional"))


FIRST_ITEM_NOTE = 3  # 1: general information, 2: accounting policies


def item_notes(bs: "BalanceSheet", pl: "IncomeStatement") -> list[ItemNote]:
    notes = [ItemNote(s, True) for s in (bs.current_assets, bs.fixed_assets, bs.intangible_assets, bs.investments,
                                         bs.current_liabilities, bs.long_term_liabilities, bs.equity)]
    notes += [ItemNote(s, False) for s in (pl.revenue, pl.cost_of_sales, pl.operating_expenses, pl.other_income, pl.other_expenses,
                                          pl.finance_costs, pl.income_tax)]
    return [note for note in notes if note.section.rows]


def number_notes(bs: "BalanceSheet", pl: "IncomeStatement") -> None:
    """Give each statement line group the number of its note, so each item refers to the notes."""
    for number, note in enumerate(item_notes(bs, pl), start=FIRST_ITEM_NOTE):
        note.section.note = number


# --- Trial balance -----------------------------------------------------------


@dataclass
class TrialBalanceRow:
    code: str
    name: str
    debit: Decimal
    credit: Decimal


@dataclass
class TrialBalance:
    as_of: date
    rows: list[TrialBalanceRow]

    @property
    def total_debit(self) -> Decimal:
        return sum((r.debit for r in self.rows), ZERO)

    @property
    def total_credit(self) -> Decimal:
        return sum((r.credit for r in self.rows), ZERO)

    @property
    def is_balanced(self) -> bool:
        return self.total_debit == self.total_credit

    def csv_rows(self):
        yield [_("Code"), _("Account"), _("Debit"), _("Credit")]
        for r in self.rows:
            yield [r.code, r.name, r.debit, r.credit]
        yield ["", _("TOTAL"), self.total_debit, self.total_credit]


def build_trial_balance(balances: list[AccountBalance], as_of: date) -> TrialBalance:
    rows = []
    for b in balances:
        net = b.net_debit
        if net:
            rows.append(TrialBalanceRow(b.account.code, b.account.name, max(net, ZERO), max(-net, ZERO)))
    return TrialBalance(as_of=as_of, rows=rows)


def _section_csv(section: Section, compare: bool = False):
    note = _("Note %(number)s") % {"number": section.note} if section.note else ""
    yield [section.title, note, ""] + ([""] if compare else [])
    for row in section.rows:
        yield [row.code, row.name, row.amount, *([row.previous] if compare else [])]
    yield ["", _("Total %(section)s") % {"section": section.title}, section.total, *([section.previous_total] if compare else [])]
