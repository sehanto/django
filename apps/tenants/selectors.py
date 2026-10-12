from django.db.models import QuerySet

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
    return Membership.objects.filter(organization=organization).select_related("user").order_by("created_at")


def available_plans() -> QuerySet[Plan]:
    return Plan.objects.all()
