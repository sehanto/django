"""Use cases for organizations (tenants), memberships and subscriptions."""

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils.translation import gettext as _

from apps.core.exceptions import PlanLimitExceeded, ValidationFailed
from apps.ledger.services import seed_chart_of_accounts
from apps.taxes.services import seed_tax_rates

from .models import Membership, Organization, Plan
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
            _("The %(plan)s plan allows only %(limit)s users. Please upgrade your plan.")
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
def remove_member(*, organization: Organization, membership_id: int, acting_user) -> None:
    membership = Membership.objects.select_for_update().get(organization=organization, pk=membership_id)
    if membership.role == Role.OWNER:
        raise ValidationFailed(_("The organization owner cannot be removed."))
    if membership.user_id == acting_user.pk:
        raise ValidationFailed(_("You cannot remove yourself."))
    membership.delete()


@transaction.atomic
def change_plan(*, organization: Organization, plan: Plan) -> Organization:
    """Switch subscription tier.

    Payment collection (e.g. Midtrans/Xendit) would hook in here; this
    implementation only validates that current usage fits the new plan.
    """
    member_count = organization.memberships.count()
    if plan.max_users is not None and member_count > plan.max_users:
        raise PlanLimitExceeded(
            _("The organization has %(count)s users, more than the %(plan)s plan allows (%(limit)s).")
            % {"count": member_count, "plan": plan.name, "limit": plan.max_users}
        )
    organization.plan = plan
    organization.save(update_fields=["plan", "updated_at"])
    return organization



@transaction.atomic
def delete_organization(*, organization: Organization) -> None:
    """Permanently remove a tenant and all of its data (offboarding).

    Business tables use PROTECT foreign keys so that ledger history cannot be
    destroyed accidentally; this use case removes rows in dependency order.
    """
    from apps.contacts.models import Contact
    from apps.core.models import DocumentSequence
    from apps.invoicing.models import Invoice, Item, Payment
    from apps.ledger.models import Account, JournalEntry
    from apps.taxes.models import PphFinalPayment, TaxRate

    for model in (PphFinalPayment, Payment, Invoice, Item):
        model.objects.filter(organization=organization).delete()
    entries = JournalEntry.objects.filter(organization=organization)
    entries.filter(reversal_of__isnull=False).delete()  # reversals point at their originals
    entries.delete()
    for model in (Contact, TaxRate, Account, DocumentSequence):
        model.objects.filter(organization=organization).delete()
    organization.delete()
