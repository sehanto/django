from dataclasses import dataclass
from datetime import date

from django.conf import settings
from django.db.models import QuerySet
from django.utils import timezone

from apps.tenants.models import Plan

from .domain import Charge, SubscriptionState, compute_charge, subscription_state
from .models import SubscriptionInvoice


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
    monthly: Charge | None
    yearly: Charge | None

    @property
    def is_free(self) -> bool:
        return self.monthly is None


def plan_offers() -> list[PlanOffer]:
    offers = []
    for plan in Plan.objects.all():
        monthly = compute_charge(plan.monthly_price) if plan.monthly_price > 0 else None
        yearly = compute_charge(plan.yearly_price) if plan.yearly_price > 0 else None
        offers.append(PlanOffer(plan=plan, monthly=monthly, yearly=yearly))
    return offers


