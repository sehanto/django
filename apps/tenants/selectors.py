from django.db.models import Exists, OuterRef, QuerySet
from django.utils import timezone

from .models import Invitation, Membership, Organization


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
    from apps.accounts.models import AuthenticatorDevice  # accounts depends on tenants; import at use

    authenticator = AuthenticatorDevice.objects.filter(user=OuterRef("user"), confirmed=True)
    return (
        Membership.objects.filter(organization=organization)
        .select_related("user")
        .annotate(has_two_factor=Exists(authenticator))
        .order_by("created_at")
    )


def pending_invitations(organization) -> QuerySet[Invitation]:
    """Invitations not yet accepted, revoked or expired (``Invitation.is_pending``)."""
    return Invitation.objects.filter(
        organization=organization, accepted_at=None, revoked_at=None, expires_at__gt=timezone.now()
    ).order_by("created_at")


def organizations_requiring_two_factor(user) -> QuerySet[Organization]:
    return organizations_for_user(user).filter(require_two_factor=True)
