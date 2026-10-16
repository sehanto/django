from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.conf import settings
from django.db.models import QuerySet
from django.utils import timezone

from apps.regions.models import Village, format_address
from apps.regions.selectors import village_by_code
from apps.tenants.models import Plan

from apps.tenants.domain import SubscriptionState

from .domain import BillingPeriod, Credit, PaidPeriod, SwitchQuote, switch_quote, unused_credit
from .models import AutoRenewal, SubscriptionInvoice


@dataclass(frozen=True)
class Seller:
    """The platform, as the seller on subscription invoices (``BILLING_SELLER_*``)."""

    name: str
    npwp: str
    street: str
    village: Village | None  # from BILLING_SELLER_VILLAGE; None when unset or not on the list

    @property
    def address(self) -> str:
        return format_address(self.street, self.village)


def platform_seller() -> Seller:
    return Seller(
        name=settings.BILLING_SELLER_NAME, npwp=settings.BILLING_SELLER_NPWP, street=settings.BILLING_SELLER_ADDRESS,
        village=village_by_code(settings.BILLING_SELLER_VILLAGE) if settings.BILLING_SELLER_VILLAGE else None,
    )


def seller_of(invoice: SubscriptionInvoice) -> dict:
    """The seller as on the invoice; invoices from before sellers were kept show today's details."""
    if invoice.seller_name:
        return {"name": invoice.seller_name, "npwp": invoice.seller_npwp, "address": invoice.seller_address}
    seller = platform_seller()
    return {"name": seller.name, "npwp": seller.npwp, "address": seller.address}


def invoices_for(organization) -> QuerySet[SubscriptionInvoice]:
    return SubscriptionInvoice.objects.filter(organization=organization).select_related("plan")


def state_of(organization, *, today: date | None = None) -> SubscriptionState:
    return organization.subscription_state(today)


@dataclass
class PlanOffer:
    plan: Plan
    monthly: SwitchQuote | None
    yearly: SwitchQuote | None
    credit: Decimal = Decimal(0)  # unused paid time on the current plan, available for this switch

    @property
    def is_free(self) -> bool:
        return self.monthly is None


def plan_offers(organization) -> list[PlanOffer]:
    """Every plan with what *this* organization would pay now (after any proration credit)."""
    today, offers = timezone.localdate(), []
    other_plans_credit = None  # the same for every plan but the current one: read the paid periods once
    for plan in Plan.objects.all():
        if plan.monthly_price <= 0:
            offers.append(PlanOffer(plan=plan, monthly=None, yearly=None))
            continue
        if plan.pk == organization.plan_id:
            credit = Credit.none()
        else:
            if other_plans_credit is None:
                other_plans_credit = switch_credit(organization, plan, today=today)
            credit = other_plans_credit
        offers.append(PlanOffer(
            plan=plan,
            monthly=switch_quote(price=price_for(plan, BillingPeriod.MONTHLY), credit=credit, period=BillingPeriod.MONTHLY, starts_on=today),
            yearly=switch_quote(price=price_for(plan, BillingPeriod.YEARLY), credit=credit, period=BillingPeriod.YEARLY, starts_on=today),
            credit=credit.amount,
        ))
    return offers


def auto_renewal_of(organization) -> AutoRenewal | None:
    return AutoRenewal.objects.filter(organization=organization).first()


def price_for(plan: Plan, period: BillingPeriod) -> Decimal:
    return plan.yearly_price if period == BillingPeriod.YEARLY else plan.monthly_price


# --- Proration ------------------------------------------------------------------------------


def paid_periods(organization, plan) -> list[PaidPeriod]:
    """Paid, not yet superseded coverage of ``plan`` (renewals can stack several)."""
    invoices = SubscriptionInvoice.objects.filter(
        organization=organization, plan=plan, status=SubscriptionInvoice.Status.PAID, superseded_on__isnull=True,
        coverage_start__isnull=False, coverage_end__isnull=False,
    )
    return [PaidPeriod(inv.coverage_start, inv.coverage_end, inv.coverage_value) for inv in invoices]


def switch_credit(organization, plan: Plan, *, today: date | None = None) -> Credit:
    """Credit for unused paid time when moving to a *different* plan (none when renewing)."""
    today = today or timezone.localdate()
    current = organization.plan
    if plan.pk == current.pk or not organization.plan_paid_until or organization.plan_paid_until <= today:
        return Credit.none()
    return unused_credit(paid_periods(organization, current), today)


def quote_for(organization, plan: Plan, period: BillingPeriod, *, today: date | None = None) -> SwitchQuote:
    today = today or timezone.localdate()
    return switch_quote(
        price=price_for(plan, period), credit=switch_credit(organization, plan, today=today), period=period, starts_on=today
    )
