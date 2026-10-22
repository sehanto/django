from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from django.db.models import Count, F, Q, QuerySet, Sum
from django.utils import timezone

from apps.core.money import ZERO

from . import coretax
from .domain import AgingBucket, aging_bucket
from .models import CoretaxConnection, CoretaxSubmission, Invoice, InvoiceKind, InvoiceStatusFilter, Item, Payment, ReturnNote


def invoices_for(
    organization, kind: str, *, query: str = "", status: str = "", contact_id: int | None = None
) -> QuerySet[Invoice]:
    qs = Invoice.objects.for_organization(organization).filter(kind=kind).select_related("contact")
    if status == InvoiceStatusFilter.OVERDUE:
        qs = qs.filter(status__in=Invoice.OPEN_STATUSES, due_date__lt=timezone.localdate())
    elif status:
        qs = qs.filter(status=status)
    if query:
        qs = qs.filter(Q(number__icontains=query) | Q(contact__name__icontains=query) | Q(contact_name__icontains=query) | Q(reference__icontains=query))
    if contact_id:
        qs = qs.filter(contact_id=contact_id)
    return qs


def invoice_detail(organization, pk: int) -> QuerySet[Invoice]:
    return (
        Invoice.objects.for_organization(organization)
        .select_related("contact", "journal_entry", "organization")
        .prefetch_related("lines__account", "lines__tax_rate", "lines__item", "payments__cash_account")
        .filter(pk=pk)
    )


def open_invoices(organization, kind: str) -> QuerySet[Invoice]:
    return invoices_for(organization, kind).filter(status__in=Invoice.OPEN_STATUSES)


def payments_for(organization, *, kind: str = "", query: str = "") -> QuerySet[Payment]:
    qs = Payment.objects.for_organization(organization).select_related("invoice__contact", "cash_account")
    if kind:
        qs = qs.filter(invoice__kind=kind)
    if query:
        qs = qs.filter(Q(number__icontains=query) | Q(invoice__number__icontains=query) | Q(invoice__contact__name__icontains=query)
            | Q(invoice__contact_name__icontains=query))
    return qs


def last_cash_account(organization):
    """The active cash or bank account of the latest payment recorded, if any."""
    payment = (
        Payment.objects.for_organization(organization).filter(cash_account__is_active=True)
        .select_related("cash_account").order_by("-created_at", "-id").first()
    )
    return payment.cash_account if payment is not None else None


def items_for(organization, *, active_only: bool = False, query: str = "") -> QuerySet[Item]:
    qs = Item.objects.for_organization(organization)
    if active_only:
        qs = qs.filter(is_active=True)
    if query:
        qs = qs.filter(Q(name__icontains=query) | Q(code__icontains=query))
    return qs


def invoices_created_this_month(organization) -> int:
    today = timezone.localdate()
    return (
        Invoice.objects.for_organization(organization)
        .filter(created_at__year=today.year, created_at__month=today.month, opening_balance=False)  # balances brought over are free
        .count()
    )


@dataclass
class AgingRow:
    contact_name: str
    buckets: dict[AgingBucket, Decimal] = field(default_factory=lambda: dict.fromkeys(AgingBucket, ZERO))

    @property
    def total(self) -> Decimal:
        return sum(self.buckets.values(), ZERO)


@dataclass
class AgingReport:
    as_of: date
    rows: list[AgingRow]
    bucket_labels: list[tuple[str, str]]

    @property
    def totals(self) -> dict[str, Decimal]:
        return {key: sum((r.buckets[key] for r in self.rows), ZERO) for key, _ in self.bucket_labels}

    @property
    def grand_total(self) -> Decimal:
        return sum(self.totals.values(), ZERO)


def aging_report(organization, kind: str) -> AgingReport:
    """Outstanding balances grouped by contact and days past due (as of today)."""
    today = timezone.localdate()
    rows: dict[int, AgingRow] = {}
    for invoice in open_invoices(organization, kind).order_by("contact__name"):
        row = rows.setdefault(invoice.contact_id, AgingRow(contact_name=invoice.contact.name))
        row.buckets[aging_bucket((today - invoice.due_date).days)] += invoice.balance_due
    return AgingReport(as_of=today, rows=list(rows.values()), bucket_labels=AgingBucket.choices)


def outstanding_summary(organization, kind: str) -> dict:
    balance = Sum(F("total") - F("amount_paid") - F("amount_credited"), default=ZERO)
    open_qs = open_invoices(organization, kind)
    overdue = open_qs.filter(due_date__lt=timezone.localdate()).aggregate(amount=balance, count=Count("id"))
    return {
        "outstanding": open_qs.aggregate(amount=balance)["amount"],
        "overdue": overdue["amount"],
        "overdue_count": overdue["count"],
    }


def status_counts(organization, kind: str) -> dict[str, int]:
    """How many documents of the kind are in each status (keys: InvoiceStatusFilter values; "" = all)."""
    qs = Invoice.objects.for_organization(organization).filter(kind=kind)
    counts = {row["status"]: row["n"] for row in qs.values("status").annotate(n=Count("id")).order_by()}
    counts[""] = sum(counts.values())
    counts[InvoiceStatusFilter.OVERDUE] = qs.filter(status__in=Invoice.OPEN_STATUSES, due_date__lt=timezone.localdate()).count()
    return counts


def vat_summary(organization, year: int) -> list[dict]:
    """Monthly PPN Keluaran vs PPN Masukan from non-void invoices, less returns (nota retur)
    in the month of the return. Sales under a PPN facility count in the sales value with
    their PPN not collected."""
    rows = (
        Invoice.objects.for_organization(organization)
        .filter(date__year=year, status__in=Invoice.ISSUED_STATUSES, opening_balance=False)
        .values("date__month", "kind")
        .annotate(tax=Sum("tax_amount"), base=Sum("subtotal"), not_collected=Sum("vat_not_collected"), luxury=Sum("luxury_tax_amount"))
    )
    returns = (
        ReturnNote.objects.for_organization(organization).filter(date__year=year, status=ReturnNote.Status.POSTED)
        .values("date__month", "kind").annotate(
            tax=Sum("tax_amount"), base=Sum("subtotal"), not_collected=Sum("vat_not_collected"), luxury=Sum("luxury_tax_amount"),
        )
    )
    # PPN on sales to VAT collectors (codes 02/03) is deposited by the buyer, not payable by us.
    collected = (
        Invoice.objects.for_organization(organization)
        .filter(date__year=year, kind=InvoiceKind.SALES, status__in=Invoice.ISSUED_STATUSES, opening_balance=False)
        .exclude(buyer_vat_collector="").values("date__month").annotate(tax=Sum("tax_amount"))
    )
    collected_returns = (
        ReturnNote.objects.for_organization(organization)
        .filter(date__year=year, kind=InvoiceKind.SALES, status=ReturnNote.Status.POSTED)
        .exclude(invoice__buyer_vat_collector="").values("date__month").annotate(tax=Sum("tax_amount"))
    )
    # PPN under a facility (codes 07/08) is reported but never charged nor payable.
    keys = ("output_base", "output", "luxury", "not_collected", "collected", "input_base", "input")
    months = {m: {"month": m, **{key: ZERO for key in keys}} for m in range(1, 13)}
    for row, sign in [*((r, 1) for r in rows), *((r, -1) for r in returns)]:
        target = months[row["date__month"]]
        prefix = "output" if row["kind"] == InvoiceKind.SALES else "input"
        target[prefix] += sign * row["tax"]
        target[f"{prefix}_base"] += sign * row["base"]
        if prefix == "output":  # PPnBM on purchases is part of their cost: only sales owe it
            target["not_collected"] += sign * row["not_collected"]
            target["luxury"] += sign * row["luxury"]
    for row, sign in [*((r, 1) for r in collected), *((r, -1) for r in collected_returns)]:
        months[row["date__month"]]["collected"] += sign * row["tax"]
    if organization.is_pkp:  # PPN impor paid at customs (PIB): input PPN of the PIB's month
        from apps.taxes.models import ImportDeclaration  # taxes reads invoicing too

        imports = (
            ImportDeclaration.objects.for_organization(organization)
            .filter(date__year=year, status=ImportDeclaration.Status.POSTED)
            .values("date__month").annotate(tax=Sum("vat"), base=Sum("customs_value"))
        )
        for row in imports:
            months[row["date__month"]]["input"] += row["tax"]
            months[row["date__month"]]["input_base"] += row["base"]
    # A retailer's sales to end consumers without an NPWP or identity document are reported in
    # one monthly total (digunggung) instead of individual fakturs: shown here as a breakdown.
    for month in months.values():
        month["aggregated_base"] = month["aggregated"] = ZERO
    if organization.is_retailer:
        anonymous = (
            Invoice.objects.for_organization(organization)
            .filter(date__year=year, kind=InvoiceKind.SALES, contact_npwp="", contact_id_number="",
                    status__in=Invoice.ISSUED_STATUSES, opening_balance=False)
            .values("date__month").annotate(tax=Sum("tax_amount"), base=Sum("subtotal"))
        )
        for row in anonymous:
            months[row["date__month"]]["aggregated_base"] += row["base"]
            months[row["date__month"]]["aggregated"] += row["tax"]
    for month in months.values():
        month["net"] = month["output"] - month["collected"] - month["input"]
    return list(months.values())


def returns_for(organization) -> QuerySet[ReturnNote]:
    return ReturnNote.objects.for_organization(organization).select_related("invoice", "cash_account", "journal_entry")


def coretax_connected(organization) -> bool:
    """Whether the organization sends to Coretax through an active connection."""
    return CoretaxConnection.objects.filter(organization=organization, is_active=True).exists()


def purchase_return_months(organization, year: int) -> set[int]:
    """Months with posted purchase returns (their Retur Faktur PM file)."""
    return set(
        ReturnNote.objects.for_organization(organization)
        .filter(kind=InvoiceKind.PURCHASE, status=ReturnNote.Status.POSTED, date__year=year)
        .values_list("date__month", flat=True)
    )


def fakturs_without_number(organization, year: int) -> dict[int, int]:
    """{month: issued sales invoices that need their own Faktur Pajak and have no number yet}: the
    ones a PKP reports as fakturs (with PPN, charged or under a facility; not exports, which have a
    PEB, nor retail sales in the month's total)."""
    if not organization.is_pkp:
        return {}
    pending = (
        Invoice.objects.for_organization(organization)
        .filter(Q(tax_amount__gt=0) | Q(vat_not_collected__gt=0), kind=InvoiceKind.SALES, date__year=year,
                status__in=Invoice.ISSUED_STATUSES, tax_invoice_number="")
        .select_related("organization", "contact")
    )
    months: dict[int, int] = {}
    for invoice in pending:
        if not invoice.is_export and not coretax.is_aggregated(invoice):
            months[invoice.date.month] = months.get(invoice.date.month, 0) + 1
    return months


def faktur_months(organization, year: int) -> set[int]:
    """Months with issued sales invoices that carry PPN, charged or under a facility (their Faktur file)."""
    if not organization.is_pkp:
        return set()
    return set(
        Invoice.objects.for_organization(organization)
        .filter(Q(tax_amount__gt=0) | Q(vat_not_collected__gt=0), kind=InvoiceKind.SALES, date__year=year,
                status__in=Invoice.ISSUED_STATUSES)
        .values_list("date__month", flat=True)
    )


def coretax_connection(organization) -> CoretaxConnection | None:
    return CoretaxConnection.objects.filter(organization=organization).first()


def coretax_submissions(organization, *, limit: int = 30) -> QuerySet[CoretaxSubmission]:
    """The latest submissions, newest first."""
    return CoretaxSubmission.objects.filter(organization=organization)[:limit]
