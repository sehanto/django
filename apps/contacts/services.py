from django.db import transaction
from django.db.models import ProtectedError

from apps.core.exceptions import ValidationFailed

from .models import Contact


@transaction.atomic
def create_contact(*, organization, **fields) -> Contact:
    return Contact.objects.create(organization=organization, **fields)


@transaction.atomic
def update_contact(*, contact: Contact, **fields) -> Contact:
    for name, value in fields.items():
        setattr(contact, name, value)
    contact.save()
    return contact


@transaction.atomic
def delete_contact(*, contact: Contact) -> None:
    try:
        contact.delete()
    except ProtectedError as exc:
        raise ValidationFailed("Kontak sudah dipakai pada transaksi; nonaktifkan saja.") from exc
