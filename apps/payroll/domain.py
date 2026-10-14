"""Payroll rules (pure functions): BPJS contributions, PPh 21 and PPh 26.

PPh 21 (PP 58/2023, PMK 168/2023):

* Permanent employees: in every month but the last of the year the employer withholds the
  monthly effective rate (TER) of the employee's category on the month's gross income, which
  includes the JKK, JKM and BPJS Kesehatan premiums the employer pays. In the last month
  (December, or the month the employee leaves) the year's tax is computed with the Art. 17
  rates on the year's net income less PTKP, and the withholding is that tax less what was
  withheld in the earlier months (it can be negative: a refund to the employee). When the
  employee became an Indonesian tax resident after January, or stops being one before
  December, the net income is annualised and the tax prorated to the months (``annualize``).
* Non-permanent employees paid monthly: the monthly TER every month, with no year-end step.
  Paid by the day: the daily TER on the average daily gross (0% up to Rp450.000, 0.5% up to
  Rp2,5 juta); above that, Art. 17 on 50% of each day's gross.
* Non-employees (fees for services, ``non_employee_pph21``): Art. 17 on 50% of the gross,
  per payment (not cumulative).
* Without an NPWP (or a NIK that serves as one) the PPh 21 is 20% higher (Art. 21(5a) UU PPh).

PPh 26: 20% of the gross pay of an employee who is not an Indonesian tax resident (final),
or the lower rate of a tax treaty when the employee has given a certificate of residence.

Severance (uang pesangon) bears final PPh 21 (PP 68/2009, PMK 16/2010): 0% up to Rp50 juta,
5% to Rp100 juta, 15% to Rp500 juta, 25% above, on the cumulative amount paid.

When the employer bears the tax, it pays a tax allowance (tunjangan PPh) equal to the tax on
the pay including that allowance (``gross_up``).
"""

from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal

from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.money import ZERO, quantize

HUNDRED = Decimal("100")
THOUSAND = Decimal("1000")


class PtkpStatus(models.TextChoices):
    """Marital status and dependents (max. 3) on 1 January: non-taxable income (PTKP)."""

    TK0 = "TK/0", _("TK/0: single, no dependents")
    TK1 = "TK/1", _("TK/1: single, 1 dependent")
    TK2 = "TK/2", _("TK/2: single, 2 dependents")
    TK3 = "TK/3", _("TK/3: single, 3 dependents")
    K0 = "K/0", _("K/0: married, no dependents")
    K1 = "K/1", _("K/1: married, 1 dependent")
    K2 = "K/2", _("K/2: married, 2 dependents")
    K3 = "K/3", _("K/3: married, 3 dependents")


# PMK 101/PMK.010/2016: Rp54 juta for the taxpayer, Rp4,5 juta for being married and per dependent.
PTKP = {
    PtkpStatus.TK0: Decimal("54000000"), PtkpStatus.TK1: Decimal("58500000"), PtkpStatus.TK2: Decimal("63000000"),
    PtkpStatus.TK3: Decimal("67500000"), PtkpStatus.K0: Decimal("58500000"), PtkpStatus.K1: Decimal("63000000"),
    PtkpStatus.K2: Decimal("67500000"), PtkpStatus.K3: Decimal("72000000"),
}


class EmployeeKind(models.TextChoices):
    PERMANENT = "permanent", _("Permanent employee (pegawai tetap)")
    NON_PERMANENT = "non_permanent", _("Non-permanent / casual worker (pegawai tidak tetap)")


class PayBasis(models.TextChoices):
    MONTHLY = "monthly", _("Monthly")
    DAILY = "daily", _("Daily")


class TaxScheme(models.TextChoices):
    """How a payslip's withholding was computed (shown on the payslip and bukti potong)."""

    TER_MONTHLY = "ter_monthly", _("PPh 21, monthly TER")
    ANNUAL = "annual", _("PPh 21, year-end (Art. 17)")
    TER_DAILY = "ter_daily", _("PPh 21, daily TER")
    # xgettext:no-python-format
    DAILY_ARTICLE_17 = "daily_art17", _("PPh 21, 50% of daily gross at Art. 17 rates")
    # xgettext:no-python-format
    PPH_26 = "pph26", _("PPh 26 (non-resident, 20%)")


class TerCategory(models.TextChoices):
    A = "A", "A"
    B = "B", "B"
    C = "C", "C"


TER_CATEGORY = {
    PtkpStatus.TK0: TerCategory.A, PtkpStatus.TK1: TerCategory.A, PtkpStatus.K0: TerCategory.A,
    PtkpStatus.TK2: TerCategory.B, PtkpStatus.TK3: TerCategory.B, PtkpStatus.K1: TerCategory.B, PtkpStatus.K2: TerCategory.B,
    PtkpStatus.K3: TerCategory.C,
}


def _table(rows: str) -> list[tuple[Decimal | None, Decimal]]:
    """``upper:rate`` pairs (gross up to and including ``upper``); the last has no upper bound."""
    table = []
    for pair in rows.split():
        upper, rate = pair.split(":")
        table.append((Decimal(upper) * 1000 if upper != "-" else None, Decimal(rate)))
    return table


# Lampiran PP 58/2023: monthly gross income (thousands of rupiah) up to which a rate (%) applies.
TER = {
    TerCategory.A: _table("""
        5400:0 5650:0.25 5950:0.5 6300:0.75 6750:1 7500:1.25 8550:1.5 9650:1.75 10050:2 10350:2.25 10700:2.5
        11050:3 11600:3.5 12500:4 13750:5 15100:6 16950:7 19750:8 24150:9 26450:10 28000:11 30050:12 32400:13
        35400:14 39100:15 43850:16 47800:17 51400:18 56300:19 62200:20 68600:21 77500:22 89000:23 103000:24
        125000:25 157000:26 206000:27 337000:28 454000:29 550000:30 695000:31 910000:32 1400000:33 -:34
    """),
    TerCategory.B: _table("""
        6200:0 6500:0.25 6850:0.5 7300:0.75 9200:1 10750:1.5 11250:2 11600:2.5 12600:3 13600:4 14950:5 16400:6
        18450:7 21850:8 26000:9 27700:10 29350:11 31450:12 33950:13 37100:14 41100:15 45800:16 49500:17 53800:18
        58500:19 64000:20 71000:21 80000:22 93000:23 109000:24 129000:25 163000:26 211000:27 374000:28 459000:29
        555000:30 704000:31 957000:32 1405000:33 -:34
    """),
    TerCategory.C: _table("""
        6600:0 6950:0.25 7350:0.5 7800:0.75 8850:1 9800:1.25 10950:1.5 11200:1.75 12050:2 12950:3 14150:4 15550:5
        17050:6 19500:7 22700:8 26600:9 28100:10 30100:11 32600:12 35400:13 38900:14 43000:15 47400:16 51200:17
        55800:18 60400:19 66700:20 74500:21 83200:22 95600:23 110000:24 134000:25 169000:26 221000:27 390000:28
        463000:29 561000:30 709000:31 965000:32 1419000:33 -:34
    """),
}

# Lampiran PP 58/2023: daily TER for non-permanent workers not paid monthly, by gross per day.
DAILY_TER = [(Decimal("450000"), Decimal("0")), (Decimal("2500000"), Decimal("0.5"))]
NON_EMPLOYEE_BASE = Decimal("50")  # % of gross: the base of PPh 21 for non-employees and high daily pay
NO_TIN_SURCHARGE = Decimal("120")  # % of the tax, without an NPWP
PPH_26_RATE = Decimal("20")

# PP 68/2009: final PPh 21 on severance, by layers of the cumulative amount paid.
SEVERANCE_LAYERS = [
    (Decimal("50000000"), Decimal("0")), (Decimal("100000000"), Decimal("5")), (Decimal("500000000"), Decimal("15")),
    (None, Decimal("25")),
]

# Art. 17(1)(a) UU PPh as amended by UU HPP: layers of taxable income (PKP) per year.
ARTICLE_17 = [
    (Decimal("60000000"), Decimal("5")), (Decimal("250000000"), Decimal("15")), (Decimal("500000000"), Decimal("25")),
    (Decimal("5000000000"), Decimal("30")), (None, Decimal("35")),
]
# Occupational costs (biaya jabatan): 5% of gross, at most Rp500.000 a month (Rp6 juta a year).
OCCUPATIONAL_RATE, OCCUPATIONAL_MONTHLY_CAP = Decimal("5"), Decimal("500000")


def _whole(value: Decimal) -> Decimal:
    return value.quantize(Decimal("1"), rounding=ROUND_FLOOR)


def ter_rate(status: str, gross: Decimal) -> Decimal:
    for upper, rate in TER[TER_CATEGORY[PtkpStatus(status)]]:
        if upper is None or gross <= upper:
            return rate
    raise AssertionError("the last TER row has no upper bound")


def monthly_pph21(status: str, gross: Decimal) -> tuple[Decimal, Decimal]:
    """(TER rate %, PPh 21) for a month other than the last of the year."""
    rate = ter_rate(status, gross)
    return rate, _whole(gross * rate / HUNDRED)


def _layered(amount: Decimal, layers) -> Decimal:
    tax, lower = ZERO, ZERO
    for upper, rate in layers:
        if amount <= lower:
            break
        layer = (min(amount, upper) if upper is not None else amount) - lower
        tax += layer * rate / HUNDRED
        if upper is None:
            break
        lower = upper
    return _whole(tax)


def severance_tax(amount: Decimal, *, paid_before: Decimal = ZERO) -> Decimal:
    """Final PPh 21 on a severance payment, after ``paid_before`` of the same severance."""
    return _layered(paid_before + amount, SEVERANCE_LAYERS) - _layered(paid_before, SEVERANCE_LAYERS)


def gross_up(tax_on) -> Decimal:
    """The tax allowance A with A = tax_on(A): the tax on pay that includes the allowance.
    ``tax_on`` is non-decreasing and below the pay, so iterating from 0 settles on the least
    such allowance."""
    allowance = ZERO
    for _ in range(200):
        tax = max(tax_on(allowance), ZERO)
        if tax == allowance:
            return allowance
        allowance = tax
    raise AssertionError("the tax allowance did not settle")


def article_17(taxable: Decimal) -> Decimal:
    return _layered(taxable, ARTICLE_17)


def with_surcharge(tax: Decimal, has_tin: bool) -> Decimal:
    """PPh 21 of a recipient without an NPWP is 20% higher."""
    return tax if has_tin or tax <= 0 else _whole(tax * NO_TIN_SURCHARGE / HUNDRED)


@dataclass(frozen=True)
class DailyRow:
    """One day's wage and its PPh 21: one bukti potong each (PMK 168/2023, while Coretax has no
    combined one), as on DJP's BP21 converter."""

    gross: Decimal
    scheme: str
    rate: Decimal | None  # daily TER %, or None at the Art. 17 rates
    tax: Decimal


def daily_rows(gross: Decimal, days: int) -> list[DailyRow]:
    """``gross`` earned in ``days`` days, split evenly (the remainder on the last day)."""
    whole = gross.quantize(Decimal("1"), rounding=ROUND_FLOOR)
    each = (whole / days).quantize(Decimal("1"), rounding=ROUND_FLOOR)
    amounts = [each] * (days - 1) + [gross - each * (days - 1)]
    rows = []
    for amount in amounts:
        for upper, rate in DAILY_TER:
            if amount <= upper:
                rows.append(DailyRow(amount, TaxScheme.TER_DAILY, rate, _whole(amount * rate / HUNDRED)))
                break
        else:
            rows.append(DailyRow(amount, TaxScheme.DAILY_ARTICLE_17, None, article_17(amount * NON_EMPLOYEE_BASE / HUNDRED)))
    return rows


def daily_pph21(gross: Decimal, days: int) -> tuple[str, Decimal | None, Decimal]:
    """(scheme, daily TER %, PPh 21) on ``gross`` earned in ``days`` days by a worker paid by the
    day: the sum of each day's bukti potong (scheme and rate of the first day)."""
    rows = daily_rows(gross, days)
    return rows[0].scheme, rows[0].rate, sum((r.tax for r in rows), ZERO)


def article_17_bracket(taxable: Decimal) -> Decimal:
    """The top Art. 17 rate reached (the "Tarif" of a progressive bukti potong row)."""
    for upper, rate in ARTICLE_17:
        if upper is None or taxable <= upper:
            return rate
    return ARTICLE_17[-1][1]


def severance_bracket(amount: Decimal) -> Decimal:
    for upper, rate in SEVERANCE_LAYERS:
        if upper is None or amount <= upper:
            return rate
    return SEVERANCE_LAYERS[-1][1]


def non_employee_pph21(gross: Decimal, *, has_tin: bool = True) -> Decimal:
    """PPh 21 on a fee paid to a non-employee: Art. 17 on 50% of the gross, per payment."""
    return with_surcharge(article_17(gross * NON_EMPLOYEE_BASE / HUNDRED), has_tin)


def pph26(gross: Decimal, rate: Decimal = PPH_26_RATE) -> Decimal:
    return _whole(gross * rate / HUNDRED)


@dataclass(frozen=True)
class AnnualTax:
    gross: Decimal
    occupational_costs: Decimal
    contributions: Decimal  # the employee's own JHT and JP
    zakat: Decimal  # compulsory religious donations through the employer
    net: Decimal  # of the months worked here
    prior_net: Decimal  # from an earlier employer of the year (its bukti potong)
    annual_net: Decimal  # both, or annualised
    ptkp: Decimal
    taxable: Decimal  # PKP, rounded down to a thousand rupiah
    annual_tax: Decimal  # Art. 17 on the PKP
    tax: Decimal  # the year's PPh 21 (prorated when annualised)
    months: int
    annualized: bool


def annual_pph21(
    status: str, gross: Decimal, contributions: Decimal, months: int, *, annualize: bool = False, zakat: Decimal = ZERO,
    prior_net: Decimal = ZERO,
) -> AnnualTax:
    """The year's PPh 21 of a permanent employee who worked ``months`` months of it. With
    ``annualize`` (tax residence began or ended during the year) the net income is annualised
    and the tax prorated to the months. ``prior_net`` is the net income from an earlier
    employer of the year that the employee handed in."""
    occupational = min(quantize(gross * OCCUPATIONAL_RATE / HUNDRED), OCCUPATIONAL_MONTHLY_CAP * months)
    net = max(gross - occupational - contributions - zakat, ZERO)
    annualize = annualize and months < 12
    annual_net = quantize((net + prior_net) * 12 / months) if annualize else net + prior_net
    ptkp = PTKP[PtkpStatus(status)]
    taxable = (max(annual_net - ptkp, ZERO) / THOUSAND).quantize(Decimal("1"), rounding=ROUND_FLOOR) * THOUSAND
    annual_tax = article_17(taxable)
    tax = _whole(annual_tax * months / 12) if annualize else annual_tax
    return AnnualTax(gross, occupational, contributions, zakat, net, prior_net, annual_net, ptkp, taxable, annual_tax, tax, months, annualize)


@dataclass(frozen=True)
class BpjsRates:
    """Contribution rates (%) and wage caps; the organization's payroll settings."""

    jkk: Decimal = Decimal("0.24")  # by the work's risk group: 0.24 to 1.74
    jkm: Decimal = Decimal("0.3")
    jht_employer: Decimal = Decimal("3.7")
    jht_employee: Decimal = Decimal("2")
    jp_employer: Decimal = Decimal("2")
    jp_employee: Decimal = Decimal("1")
    health_employer: Decimal = Decimal("4")
    health_employee: Decimal = Decimal("1")
    jp_wage_cap: Decimal = Decimal("10547400")  # adjusted every year by BPJS Ketenagakerjaan
    health_wage_cap: Decimal = Decimal("12000000")


@dataclass(frozen=True)
class Contributions:
    jkk: Decimal = ZERO
    jkm: Decimal = ZERO
    jht_employer: Decimal = ZERO
    jp_employer: Decimal = ZERO
    health_employer: Decimal = ZERO
    jht_employee: Decimal = ZERO
    jp_employee: Decimal = ZERO
    health_employee: Decimal = ZERO

    @property
    def employer(self) -> Decimal:
        return self.jkk + self.jkm + self.jht_employer + self.jp_employer + self.health_employer

    @property
    def employee(self) -> Decimal:
        return self.jht_employee + self.jp_employee + self.health_employee

    @property
    def taxable_premiums(self) -> Decimal:
        """Premiums the employer pays that are the employee's income for PPh 21 (JKK, JKM, health)."""
        return self.jkk + self.jkm + self.health_employer


def contributions(wage: Decimal, rates: BpjsRates, *, employment: bool, health: bool) -> Contributions:
    """BPJS Ketenagakerjaan (``employment``) and BPJS Kesehatan (``health``) on the month's wage
    (salary plus fixed allowances), JP and health capped at their wage limits."""
    def part(base, rate):
        return _whole(base * rate / HUNDRED)

    jp_base, health_base = min(wage, rates.jp_wage_cap), min(wage, rates.health_wage_cap)
    values = {}
    if employment:
        values.update(
            jkk=part(wage, rates.jkk), jkm=part(wage, rates.jkm), jht_employer=part(wage, rates.jht_employer),
            jht_employee=part(wage, rates.jht_employee), jp_employer=part(jp_base, rates.jp_employer),
            jp_employee=part(jp_base, rates.jp_employee),
        )
    if health:
        values.update(health_employer=part(health_base, rates.health_employer), health_employee=part(health_base, rates.health_employee))
    return Contributions(**values)
