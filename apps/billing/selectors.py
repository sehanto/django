from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.conf import settings
from django.db.models import QuerySet
from django.utils import timezone

from apps.regions.models import Village, format_address
from apps.regions.selectors import village_by_code
from apps.tenants.models import Plan

from .domain import BillingPeriod, SubscriptionState, SwitchQuote, subscription_state
from .models import SubscriptionInvoice


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
    return subscription_state(
        is_free_plan=organization.plan.monthly_price <= 0,
        paid_until=organization.plan_paid_until,
        today=today or timezone.localdate(),
        reminder_days=settings.BILLING_REMINDER_DAYS,
        grace_days=settings.BILLING_GRACE_DAYS,
    )


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
    from .services import quote_for, switch_credit  # services import selectors

    offers = []
    for plan in Plan.objects.all():
        if plan.monthly_price <= 0:
            offers.append(PlanOffer(plan=plan, monthly=None, yearly=None))
            continue
        offers.append(PlanOffer(
            plan=plan,
            monthly=quote_for(organization, plan, BillingPeriod.MONTHLY),
            yearly=quote_for(organization, plan, BillingPeriod.YEARLY),
            credit=switch_credit(organization, plan).amount,
        ))
    return offers
