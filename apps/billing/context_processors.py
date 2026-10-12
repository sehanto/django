from datetime import timedelta

from django.conf import settings

from apps.tenants.roles import Permission

from .domain import SubscriptionState
from .selectors import state_of


def subscription_notice(request):
    """Expose an expiry warning to the people who can pay (owners/admins)."""
    membership = getattr(request, "membership", None)
    if membership is None or not membership.has_permission(Permission.MANAGE):
        return {}
    state = state_of(request.organization)
    if state not in (SubscriptionState.EXPIRING, SubscriptionState.GRACE):
        return {}
    organization = request.organization
    return {
        "subscription_notice": {
            "state": state,
            "states": SubscriptionState,
            "plan": organization.plan.name,
            "paid_until": organization.plan_paid_until,
            "lapse_on": organization.plan_paid_until + timedelta(days=settings.BILLING_GRACE_DAYS + 1),
        }
    }
