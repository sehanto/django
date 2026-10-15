from datetime import timedelta

from django.conf import settings

from apps.tenants.domain import SubscriptionState
from apps.tenants.roles import Permission

from .selectors import state_of


def subscription_notice(request):
    """The trial's or paid period's end for the people who can pay (owners/admins), and that the
    organization is read-only for everyone (they would otherwise only find out when saving)."""
    membership = getattr(request, "membership", None)
    if membership is None:
        return {}
    state = state_of(request.organization)
    can_pay = membership.has_permission(Permission.MANAGE)
    shown = {SubscriptionState.TRIAL, SubscriptionState.EXPIRING, SubscriptionState.GRACE} if can_pay else set()
    if state not in shown | {SubscriptionState.LAPSED}:
        return {}
    organization = request.organization
    return {
        "subscription_notice": {
            "state": state,
            "states": SubscriptionState,
            "plan": organization.plan.name,
            "paid_until": organization.plan_paid_until,
            "lapse_on": organization.plan_paid_until + timedelta(days=settings.BILLING_GRACE_DAYS + 1) if organization.plan_paid_until else None,
            "can_pay": can_pay,
        }
    }
