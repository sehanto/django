from datetime import date

import pytest
from django.db import transaction

from apps.core.sequences import next_document_number


def test_numbers_increment_per_prefix_and_month(organization):
    with transaction.atomic():
        assert next_document_number(organization, "INV", date(2026, 1, 5)) == "INV/2026/01/0001"
        assert next_document_number(organization, "INV", date(2026, 1, 9)) == "INV/2026/01/0002"
        assert next_document_number(organization, "INV", date(2026, 2, 1)) == "INV/2026/02/0001"
        assert next_document_number(organization, "BILL", date(2026, 1, 5)) == "BILL/2026/01/0001"


def test_sequences_are_isolated_per_tenant(organization, other_organization):
    with transaction.atomic():
        next_document_number(organization, "INV", date(2026, 1, 5))
        assert next_document_number(other_organization, "INV", date(2026, 1, 5)) == "INV/2026/01/0001"


@pytest.mark.django_db(transaction=True)
def test_requires_transaction(organization):
    with pytest.raises(RuntimeError):
        next_document_number(organization, "INV", date(2026, 1, 5))
