"""Shared by commands that work on every organization or on one (``--organization ID``)."""

from django.core.management.base import CommandError


def selected_organizations(organization_id: int | None) -> list:
    """Every organization, oldest first, or just the one asked for (which must exist)."""
    from apps.tenants.models import Organization  # core is below tenants

    organizations = Organization.objects.order_by("pk")
    if organization_id is None:
        return list(organizations)
    selected = list(organizations.filter(pk=organization_id))
    if not selected:
        raise CommandError(f"Organization {organization_id} does not exist.")
    return selected
