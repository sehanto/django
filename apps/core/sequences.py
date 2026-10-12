"""Gap-free document numbering in the common Indonesian format.

Example: ``INV/2026/10/0001``. Must be called inside a transaction; the row
lock guarantees uniqueness under concurrent requests.
"""

from datetime import date

from django.db import transaction

from .models import DocumentSequence


def format_document_number(prefix: str, on: date, number: int) -> str:
    return f"{prefix}/{on.year}/{on.month:02d}/{number:04d}"


def next_document_number(organization, prefix: str, on: date) -> str:
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("next_document_number() must run inside transaction.atomic().")
    sequence, _ = DocumentSequence.objects.get_or_create(
        organization=organization, prefix=prefix, year=on.year, month=on.month
    )
    sequence = DocumentSequence.objects.select_for_update().get(pk=sequence.pk)
    sequence.last_number += 1
    sequence.save(update_fields=["last_number", "updated_at"])
    return format_document_number(prefix, on, sequence.last_number)
