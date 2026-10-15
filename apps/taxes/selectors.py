from datetime import date
from decimal import Decimal

from django.db.models import QuerySet, Sum
from django.db.models.functions import ExtractMonth

from apps.core.money import ZERO
from apps.ledger.domain import AccountCategory
from apps.ledger.selectors import posted_lines

from .domain import PayerWithholding, PphFinalMonth, compute_pph_final
from .models import ImportDeclaration, PphFinalAccrual, PphFinalPayment, SelfDeposit, TaxRate, WithholdingDeposit


def tax_rate_is_used(rate: TaxRate) -> bool:
    """Used on an issued invoice: its name, rate and DPP are locked (taxes migration 0007)."""
    from apps.invoicing.models import Invoice, InvoiceLine  # invoicing depends on taxes

    return InvoiceLine.objects.filter(tax_rate=rate).exclude(invoice__status=Invoice.Status.DRAFT).exists()


def tax_rates_for(organization, *, active_only: bool = False) -> QuerySet[TaxRate]:
    qs = TaxRate.objects.for_organization(organization)
    return qs.filter(is_active=True) if active_only else qs


def monthly_gross_turnover(organization, year: int) -> list[Decimal]:
    """Operating revenue (net of contra accounts such as sales returns) per month."""
    lines = posted_lines(organization, date_from=date(year, 1, 1), date_to=date(year, 12, 31)).filter(
        account__category=AccountCategory.REVENUE
    )
    turnover = [ZERO] * 12
    for row in lines.annotate(month=ExtractMonth("entry__date")).values("month").annotate(
        debit=Sum("debit"), credit=Sum("credit")
    ):
        turnover[row["month"] - 1] = row["credit"] - row["debit"]
    return turnover


def pph_final_schedule(organization, year: int) -> list[PphFinalMonth]:
    return compute_pph_final(
        monthly_gross_turnover(organization, year), is_individual=organization.is_individual_taxpayer
    )


def pph_final_withheld(organization, year: int) -> dict[int, Decimal]:
    """PPh Final buyers withheld from receipts, per month of the receipt: they deposit that
    part of the month's PPh Final for us, so our own deposit is smaller. A refund gives back
    what was withheld on it, in the month of the return."""
    from apps.invoicing.models import IncomeTaxWithheld, Payment, ReturnNote  # invoicing depends on taxes

    withheld: dict[int, Decimal] = {}
    for model, sign in ((Payment, 1), (ReturnNote, -1)):
        rows = (
            model.objects.for_organization(organization)
            .filter(status=model.Status.POSTED, income_tax_kind=IncomeTaxWithheld.PPH_FINAL, date__year=year)
            .annotate(month=ExtractMonth("date")).values("month").annotate(amount=Sum("income_tax_withheld"))
        )
        for row in rows:
            withheld[row["month"]] = withheld.get(row["month"], ZERO) + sign * row["amount"]
    return {month: amount for month, amount in withheld.items() if amount}


def pph_final_payments(organization, year: int) -> dict[int, PphFinalPayment]:
    return {
        p.month: p
        for p in PphFinalPayment.objects.for_organization(organization)
        .filter(year=year, status=PphFinalPayment.Status.POSTED)
        .select_related("journal_entry")
    }


def pph_final_accruals(organization, year: int) -> dict[int, PphFinalAccrual]:
    return {
        a.month: a
        for a in PphFinalAccrual.objects.for_organization(organization)
        .filter(year=year, status=PphFinalAccrual.Status.POSTED)
        .select_related("journal_entry")
    }


def withholding_due(organization, year: int) -> dict[tuple[str, int], Decimal]:
    """Income tax withheld per (kind, month): from suppliers (by the payment's month, less what
    refunds of the month gave back) and from salaries: the deposit due for that month."""
    from apps.invoicing.models import InvoiceKind, Payment, ReturnNote  # invoicing depends on taxes

    due: dict[tuple[str, int], Decimal] = {}
    for model, sign in ((Payment, 1), (ReturnNote, -1)):
        kind_filter = {"invoice__kind": InvoiceKind.PURCHASE} if model is Payment else {"kind": InvoiceKind.PURCHASE}
        rows = (
            model.objects.for_organization(organization)
            .filter(status=model.Status.POSTED, income_tax_kind__in=PayerWithholding.values, date__year=year, **kind_filter)
            .annotate(month=ExtractMonth("date")).values("income_tax_kind", "month").annotate(amount=Sum("income_tax_withheld"))
        )
        for row in rows:
            key = (row["income_tax_kind"], row["month"])
            due[key] = due.get(key, ZERO) + sign * row["amount"]
    from apps.payroll.models import PayrollRun  # PPh 21 withheld from salaries

    for run in PayrollRun.objects.for_organization(organization).filter(year=year, status=PayrollRun.Status.POSTED):
        for kind, amount in ((PayerWithholding.PPH_21, run.pph21), (PayerWithholding.PPH_26, run.pph26)):
            if amount:
                due[(kind.value, run.month)] = due.get((kind.value, run.month), ZERO) + amount
    return due


def withholdings_in_year(organization, year: int):
    """Payments to suppliers with income tax withheld in a year: one bukti potong each."""
    from apps.invoicing.models import InvoiceKind, Payment

    return Payment.objects.for_organization(organization).filter(
        status=Payment.Status.POSTED, invoice__kind=InvoiceKind.PURCHASE, income_tax_kind__in=PayerWithholding.values, date__year=year,
    )


def withholdings(organization, year: int, month: int):
    return withholdings_in_year(organization, year).filter(date__month=month).select_related("invoice", "invoice__contact").order_by("date", "id")


def withholding_deposits(organization, year: int) -> dict[tuple[str, int], WithholdingDeposit]:
    return {
        (d.kind, d.month): d
        for d in WithholdingDeposit.objects.for_organization(organization)
        .filter(year=year, status=WithholdingDeposit.Status.POSTED).select_related("journal_entry")
    }


def import_declarations_in(organization, year: int) -> QuerySet[ImportDeclaration]:
    return ImportDeclaration.objects.for_organization(organization).filter(date__year=year).select_related("journal_entry", "bill")


def self_deposits_in(organization, year: int) -> QuerySet[SelfDeposit]:
    return SelfDeposit.objects.for_organization(organization).filter(year=year).select_related("payment_entry", "invoice")
