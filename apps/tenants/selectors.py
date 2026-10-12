from django.db.models import Exists, OuterRef, QuerySet
from django_otp.plugins.otp_totp.models import TOTPDevice

from .models import Membership, Organization, Plan


def resolve_membership(user, organization_id=None) -> Membership | None:
    memberships = Membership.objects.select_related("organization", "organization__plan").filter(user=user)
    if organization_id:
        membership = memberships.filter(organization_id=organization_id).first()
        if membership:
            return membership
    return memberships.order_by("created_at").first()


def organizations_for_user(user) -> QuerySet[Organization]:
    return Organization.objects.filter(memberships__user=user).order_by("name")


def members_of(organization) -> QuerySet[Membership]:
    """Members with ``has_two_factor`` (a confirmed authenticator) for the team page."""
    authenticator = TOTPDevice.objects.filter(user=OuterRef("user"), confirmed=True)
    return (
        Membership.objects.filter(organization=organization)
        .select_related("user")
        .annotate(has_two_factor=Exists(authenticator))
        .order_by("created_at")
    )


def organizations_requiring_two_factor(user) -> QuerySet[Organization]:
    return organizations_for_user(user).filter(require_two_factor=True)


def available_plans() -> QuerySet[Plan]:
    return Plan.objects.all()
