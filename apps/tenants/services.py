"""Use cases for organizations (tenants), memberships and subscriptions."""

import secrets
from datetime import date, timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.db import transaction
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone, translation
from django.utils.translation import gettext as _
from django.utils.translation import ngettext

from apps.audit import services as audit
from apps.audit.domain import AuditAction
from apps.core.crypto import keyed_hash, keyed_hash_candidates
from apps.core.exceptions import PlanLimitExceeded, ValidationFailed
from apps.ledger.services import seed_chart_of_accounts
from apps.taxes.services import seed_tax_rates

from .models import Invitation, Membership, Organization, Plan
from .roles import Role

User = get_user_model()


def get_default_plan() -> Plan:
    plan = Plan.objects.filter(is_default=True).first()
    if plan is None:
        plan, _ = Plan.objects.get_or_create(
            code="gratis",
            defaults={"name": "Gratis", "max_users": 1, "max_invoices_per_month": 30, "is_default": True},
        )
    return plan


@transaction.atomic
def create_organization(*, owner, name: str, legal_form: str, **profile) -> Organization:
    """Create a tenant, make ``owner`` its owner and seed accounting master data."""
    organization = Organization.objects.create(
        name=name,
        legal_form=legal_form,
        plan=get_default_plan(),
        **profile,
    )
    Membership.objects.create(user=owner, organization=organization, role=Role.OWNER)
    seed_chart_of_accounts(organization)
    seed_tax_rates(organization)
    audit.record(AuditAction.ORGANIZATION_CREATED, actor=owner, organization=organization, target=organization.name)
    return organization


@transaction.atomic
def register_owner_with_organization(
    *, email: str, password: str, full_name: str, organization_name: str, legal_form: str, language: str | None = None
):
    extra = {"language": language} if language else {}
    user = User.objects.create_user(email=email, password=password, full_name=full_name, **extra)
    organization = create_organization(owner=user, name=organization_name, legal_form=legal_form)
    return user, organization


def _ensure_user_capacity(organization: Organization, plan: Plan | None = None) -> None:
    plan = plan or organization.plan
    if plan.max_users is not None and organization.memberships.count() >= plan.max_users:
        raise PlanLimitExceeded(
            ngettext(
                "The %(plan)s plan allows only %(limit)s user. Please upgrade your plan.",
                "The %(plan)s plan allows only %(limit)s users. Please upgrade your plan.",
                plan.max_users,
            )
            % {"plan": plan.name, "limit": plan.max_users}
        )


@transaction.atomic
def add_member(*, organization: Organization, email: str, role: str) -> Membership:
    user = User.objects.filter(email__iexact=email).first()
    if user is None:
        raise ValidationFailed(_("No user with that email is registered yet. Ask them to sign up first."))
    if Membership.objects.filter(organization=organization, user=user).exists():
        raise ValidationFailed(_("This user is already a member of the organization."))
    if role == Role.OWNER:
        raise ValidationFailed(_("The owner role cannot be given to a new member."))
    _ensure_user_capacity(organization)
    return Membership.objects.create(organization=organization, user=user, role=role)


@transaction.atomic
def invite_member(*, organization: Organization, email: str, role: str, invited_by) -> Membership | Invitation:
    """Add a registered user at once; email anyone else an invitation link (valid for
    ``Invitation.VALID_FOR_DAYS``) that adds them once they sign up with that address."""
    if User.objects.filter(email__iexact=email).exists():
        return add_member(organization=organization, email=email, role=role)
    if role == Role.OWNER:
        raise ValidationFailed(_("The owner role cannot be given to a new member."))
    _ensure_user_capacity(organization)
    now = timezone.now()
    Invitation.objects.filter(organization=organization, email__iexact=email, accepted_at=None, revoked_at=None).update(revoked_at=now)
    token = secrets.token_urlsafe(32)
    invitation = Invitation.objects.create(
        organization=organization, email=email.strip().lower(), role=role, token_hash=keyed_hash(token), invited_by=invited_by,
        expires_at=now + timedelta(days=Invitation.VALID_FOR_DAYS),
    )
    url = settings.SITE_URL.rstrip("/") + reverse("tenants:invitation", args=[token])
    context = {
        "organization": organization.name, "invited_by": invited_by.get_full_name(), "role": Role(role).label, "url": url,
        "days": Invitation.VALID_FOR_DAYS, "email": invitation.email, "site_name": settings.SITE_NAME,
        "signup_url": settings.SITE_URL.rstrip("/") + reverse("accounts:signup"),
    }
    subject = "".join(render_to_string("tenants/emails/invitation_subject.txt", context).splitlines())
    send_mail(subject, render_to_string("tenants/emails/invitation.txt", context), settings.DEFAULT_FROM_EMAIL, [invitation.email])
    return invitation


def find_invitation(token: str) -> Invitation | None:
    return Invitation.objects.select_related("organization").filter(token_hash__in=keyed_hash_candidates(token)).first()


@transaction.atomic
def accept_invitation(*, token: str, user) -> Membership:
    invitation = find_invitation(token)
    if invitation is None or not invitation.is_pending:
        raise ValidationFailed(_("This invitation is no longer valid. Ask for a new one."))
    invitation = Invitation.objects.select_for_update().get(pk=invitation.pk)
    if user.email.lower() != invitation.email.lower():
        raise ValidationFailed(_("This invitation was sent to %(email)s. Sign in with that address to accept it.") % {"email": invitation.email})
    organization = invitation.organization
    if Membership.objects.filter(organization=organization, user=user).exists():
        raise ValidationFailed(_("You are already a member of this organization."))
    _ensure_user_capacity(organization)
    membership = Membership.objects.create(organization=organization, user=user, role=invitation.role)
    invitation.accepted_at, invitation.accepted_by = timezone.now(), user
    invitation.save(update_fields=["accepted_at", "accepted_by", "updated_at"])
    return membership


@transaction.atomic
def revoke_invitation(*, organization: Organization, invitation_id: int) -> Invitation:
    invitation = Invitation.objects.select_for_update().get(organization=organization, pk=invitation_id)
    if invitation.is_pending:
        invitation.revoked_at = timezone.now()
        invitation.save(update_fields=["revoked_at", "updated_at"])
    return invitation


@transaction.atomic
def remove_member(*, organization: Organization, membership_id: int, acting_user) -> Membership:
    membership = Membership.objects.select_for_update().get(organization=organization, pk=membership_id)
    if membership.role == Role.OWNER:
        raise ValidationFailed(_("The organization owner cannot be removed."))
    if membership.user_id == acting_user.pk:
        raise ValidationFailed(_("You cannot remove yourself."))
    membership.delete()
    return membership


def ensure_usage_fits(*, organization: Organization, plan: Plan) -> None:
    """Refuse a plan whose limits the organization already exceeds."""
    member_count = organization.memberships.count()
    if plan.max_users is not None and member_count > plan.max_users:
        raise PlanLimitExceeded(
            ngettext(
                "The organization has %(count)s user, more than the %(plan)s plan allows (%(limit)s).",
                "The organization has %(count)s users, more than the %(plan)s plan allows (%(limit)s).",
                member_count,
            )
            % {"count": member_count, "plan": plan.name, "limit": plan.max_users}
        )


@transaction.atomic
def set_subscription(*, organization: Organization, plan: Plan, paid_until: date | None) -> Organization:
    """Record the organization's effective plan. Payment is handled by ``apps.billing``."""
    if organization.plan_id != plan.pk:
        audit.record(
            AuditAction.PLAN_CHANGED, organization=organization, target=plan.name,
            previous_plan=organization.plan.name, paid_until=paid_until,
        )
    organization.plan = plan
    organization.plan_paid_until = paid_until
    organization.save(update_fields=["plan", "plan_paid_until", "updated_at"])
    return organization


@transaction.atomic
def delete_organization(*, organization: Organization) -> None:
    """Permanently remove a tenant and all of its data (offboarding).

    Business tables use PROTECT foreign keys so that ledger history cannot be
    destroyed accidentally; this use case removes rows in dependency order.
    Only the schema owner may wipe the ledger, so this runs from
    ``manage.py delete_organization``, never from the web app.
    """
    from apps.contacts.models import Contact
    from apps.core.models import DocumentSequence
    from apps.invoicing.models import Item
    from apps.ledger.services import purge_organization_ledger
    from apps.taxes.models import TaxRate

    # Invoices, payments, PPh Final payments, the ledger and the chart of accounts are
    # guarded in the database; only the owner's purge function can remove them. Items still
    # point at the accounts until they are deleted below, in the same transaction (the
    # foreign keys are checked at commit).
    purge_organization_ledger(organization)
    for model in (Item, Contact, TaxRate, DocumentSequence):
        model.objects.filter(organization=organization).delete()
    organization.delete()


def notify_two_factor_requirement(*, organization: Organization, enabled_by) -> int:
    """Email members who don't use 2FA yet that the organization now requires it."""
    from apps.accounts import two_factor  # accounts depends on tenants; import at use

    sign_in_url = settings.SITE_URL.rstrip("/") + reverse("accounts:security")
    notified = 0
    for membership in Membership.objects.filter(organization=organization).select_related("user"):
        member = membership.user
        if member.pk == enabled_by.pk or not member.is_active or two_factor.is_enabled(member):
            continue
        context = {"member": member, "organization": organization.name, "enabled_by": enabled_by.get_full_name(),
                   "url": sign_in_url, "site_name": settings.SITE_NAME}
        with translation.override(member.language):
            subject = "".join(render_to_string("tenants/emails/two_factor_required_subject.txt", context).splitlines())
            body = render_to_string("tenants/emails/two_factor_required.txt", context)
        send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [member.email])
        notified += 1
    return notified
