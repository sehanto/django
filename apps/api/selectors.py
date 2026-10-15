"""What the API lists: the apps' own selectors, with the API's filters and a stable order (for paging)."""

from django.db.models import QuerySet

from apps.contacts.models import Contact
from apps.contacts.selectors import contacts_for
from apps.invoicing.models import Invoice
from apps.invoicing.selectors import items_for
from apps.ledger.selectors import journal_entries_for
from apps.taxes.selectors import tax_rates_for

from .models import ApiKey


def contacts(organization, *, kind: str = "", province: str = "") -> QuerySet[Contact]:
    """``kind``: customers or suppliers, each including contacts that are both."""
    queryset = contacts_for(organization).select_related("village__district__regency").order_by("name", "id")
    if kind:
        queryset = queryset.filter(kind__in=[kind, Contact.Kind.BOTH])
    if province:
        queryset = queryset.filter(village__district__regency__province_id=province)
    return queryset


def items(organization):
    return items_for(organization).order_by("name", "id")


def tax_rates(organization):
    return tax_rates_for(organization).order_by("name")


def invoices(organization, *, kind: str = "", status: str = "") -> QuerySet[Invoice]:
    queryset = Invoice.objects.for_organization(organization).select_related("contact").order_by("-date", "-id")
    if kind:
        queryset = queryset.filter(kind=kind)
    if status:
        queryset = queryset.filter(status=status)
    return queryset


def journal_entries(organization):
    return journal_entries_for(organization).order_by("-date", "-id")


def api_keys(organization) -> QuerySet[ApiKey]:
    return ApiKey.objects.filter(organization=organization).select_related("user")
