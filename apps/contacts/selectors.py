from django.db.models import Q, QuerySet

from .models import Contact

CUSTOMER_KINDS = [Contact.Kind.CUSTOMER, Contact.Kind.BOTH]
SUPPLIER_KINDS = [Contact.Kind.SUPPLIER, Contact.Kind.BOTH]


def contacts_for(organization, *, query: str = "", kind: str = "") -> QuerySet[Contact]:
    qs = Contact.objects.for_organization(organization)
    if kind:
        qs = qs.filter(kind=kind)
    if query:
        qs = qs.filter(Q(name__icontains=query) | Q(email__icontains=query) | Q(npwp__icontains=query))
    return qs


def customers_for(organization) -> QuerySet[Contact]:
    return contacts_for(organization).filter(is_active=True, kind__in=CUSTOMER_KINDS)


def suppliers_for(organization) -> QuerySet[Contact]:
    return contacts_for(organization).filter(is_active=True, kind__in=SUPPLIER_KINDS)
