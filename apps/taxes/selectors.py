from datetime import date
from decimal import Decimal

from django.db.models import QuerySet, Sum
from django.db.models.functions import ExtractMonth

from apps.core.money import ZERO
from apps.ledger.domain import AccountCategory
from apps.ledger.selectors import posted_lines

from .domain import PphFinalMonth, compute_pph_final
from .models import PphFinalPayment, TaxRate


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


def pph_final_payments(organization, year: int) -> dict[int, PphFinalPayment]:
    return {
        p.month: p
        for p in PphFinalPayment.objects.for_organization(organization)
        .filter(year=year, status=PphFinalPayment.Status.POSTED)
        .select_related("journal_entry")
    }

