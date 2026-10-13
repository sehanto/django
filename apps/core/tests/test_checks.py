"""Deploy checks (manage.py check --deploy)."""

import pytest
from django.test import override_settings

from apps.core.checks import check_billing_seller


@pytest.mark.parametrize(("npwp", "address", "warned"), [
    ("012345678901000", "Jl. Sudirman No. 1, Jakarta", False),
    ("0123456789012345", "Jl. Sudirman No. 1, Jakarta", False),
    ("0000000000000000", "Jl. Sudirman No. 1, Jakarta", True),  # all zeros
    ("", "Jl. Sudirman No. 1, Jakarta", True),
    ("12345", "Jl. Sudirman No. 1, Jakarta", True),
    ("0123456789012345", "Jl. Contoh No. 1, Jakarta (contoh)", True),
    ("0123456789012345", "", True),
])
def test_deploy_check_flags_placeholder_seller_details(npwp, address, warned):
    with override_settings(BILLING_SELLER_NAME="PT BukuUMKM Indonesia", BILLING_SELLER_NPWP=npwp, BILLING_SELLER_ADDRESS=address):
        assert [w.id for w in check_billing_seller(None)] == (["core.W003"] if warned else [])
