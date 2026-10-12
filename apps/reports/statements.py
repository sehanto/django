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


@dataclass
class Section:
    title: str
    rows: list[StatementRow] = field(default_factory=list)

    @property
    def total(self) -> Decimal:
        return sum((r.amount for r in self.rows), ZERO)


def _section(title: str, balances: Iterable[AccountBalance], categories: set[str], sign_type: AccountType) -> Section:
    """Rows are signed by the *section's* normal balance, so contra accounts reduce it."""
    section = Section(title)
    for b in balances:
        if b.account.category not in categories:
            continue
        amount = b.net_debit if sign_type.normal_balance == NormalBalance.DEBIT else -b.net_debit
        if amount:
            section.rows.append(StatementRow(b.account.code, b.account.name, amount))
    return section


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
    income_tax: Section

    @property
    def gross_profit(self) -> Decimal:
        return self.revenue.total - self.cost_of_sales.total

    @property
    def operating_profit(self) -> Decimal:
        return self.gross_profit - self.operating_expenses.total

    @property
    def profit_before_tax(self) -> Decimal:
        return self.operating_profit + self.other_income.total - self.other_expenses.total

    @property
    def net_profit(self) -> Decimal:
        return self.profit_before_tax - self.income_tax.total

    def csv_rows(self):
        yield [_("Income statement"), _("%(start)s to %(end)s") % {"start": f"{self.date_from:%d-%m-%Y}", "end": f"{self.date_to:%d-%m-%Y}"}, ""]
        for section, subtotal in [
            (self.revenue, None),
            (self.cost_of_sales, (_("Gross profit"), self.gross_profit)),
            (self.operating_expenses, (_("Operating profit"), self.operating_profit)),
            (self.other_income, None),
            (self.other_expenses, (_("Profit before tax"), self.profit_before_tax)),
            (self.income_tax, (_("Net profit"), self.net_profit)),
        ]:
            yield from _section_csv(section)
            if subtotal:
                yield ["", subtotal[0], subtotal[1]]


def build_income_statement(balances: list[AccountBalance], date_from: date, date_to: date) -> IncomeStatement:
    rev, exp = AccountType.REVENUE, AccountType.EXPENSE
    return IncomeStatement(
        date_from=date_from,
        date_to=date_to,
        revenue=_section(_("Operating revenue"), balances, {C.REVENUE}, rev),
        cost_of_sales=_section(_("Cost of sales"), balances, {C.COST_OF_SALES}, exp),
        operating_expenses=_section(_("Operating expenses"), balances, {C.OPERATING_EXPENSE}, exp),
        other_income=_section(_("Other income"), balances, {C.OTHER_INCOME}, rev),
        other_expenses=_section(_("Other expenses"), balances, {C.OTHER_EXPENSE}, exp),
        income_tax=_section(_("Income tax expense"), balances, {C.INCOME_TAX}, exp),
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
    current_liabilities: Section
    long_term_liabilities: Section
    equity: Section
    prior_years_earnings: Decimal
    current_year_earnings: Decimal

    @property
    def total_assets(self) -> Decimal:
        return self.current_assets.total + self.fixed_assets.total

    @property
    def total_liabilities(self) -> Decimal:
        return self.current_liabilities.total + self.long_term_liabilities.total

    @property
    def total_equity(self) -> Decimal:
        return self.equity.total + self.prior_years_earnings + self.current_year_earnings

    @property
    def total_liabilities_and_equity(self) -> Decimal:
        return self.total_liabilities + self.total_equity

    @property
    def is_balanced(self) -> bool:
        return self.total_assets == self.total_liabilities_and_equity

    def csv_rows(self):
        yield [_("Statement of financial position"), _("as of %(date)s") % {"date": f"{self.as_of:%d-%m-%Y}"}, ""]
        yield from _section_csv(self.current_assets)
        yield from _section_csv(self.fixed_assets)
        yield ["", _("TOTAL ASSETS"), self.total_assets]
        yield from _section_csv(self.current_liabilities)
        yield from _section_csv(self.long_term_liabilities)
        yield ["", _("Total liabilities"), self.total_liabilities]
        yield from _section_csv(self.equity)
        yield ["", _("Prior years' retained earnings (not yet closed)"), self.prior_years_earnings]
        yield ["", _("Current year profit"), self.current_year_earnings]
        yield ["", _("TOTAL LIABILITIES AND EQUITY"), self.total_liabilities_and_equity]


CURRENT_ASSET_CATEGORIES = {C.CASH_BANK, C.RECEIVABLE, C.INVENTORY, C.OTHER_CURRENT_ASSET}


def build_balance_sheet(
    balances_to_date: list[AccountBalance], balances_before_year: list[AccountBalance], as_of: date
) -> BalanceSheet:
    """``balances_before_year`` are cumulative balances up to the day before the fiscal year starts."""
    asset, liability, equity = AccountType.ASSET, AccountType.LIABILITY, AccountType.EQUITY
    prior = net_income(balances_before_year)
    return BalanceSheet(
        as_of=as_of,
        current_assets=_section(_("Current assets"), balances_to_date, CURRENT_ASSET_CATEGORIES, asset),
        fixed_assets=_section(_("Fixed assets"), balances_to_date, {C.FIXED_ASSET}, asset),
        current_liabilities=_section(_("Current liabilities"), balances_to_date, {C.CURRENT_LIABILITY}, liability),
        long_term_liabilities=_section(_("Long-term liabilities"), balances_to_date, {C.LONG_TERM_LIABILITY}, liability),
        equity=_section(_("Capital"), balances_to_date, {C.EQUITY}, equity),
        prior_years_earnings=prior,
        current_year_earnings=net_income(balances_to_date) - prior,
    )


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


def _section_csv(section: Section):
    yield [section.title, "", ""]
    for row in section.rows:
        yield [row.code, row.name, row.amount]
    yield ["", _("Total %(section)s") % {"section": section.title}, section.total]
