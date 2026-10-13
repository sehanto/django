import pytest

from apps.contacts.validators import format_npwp


@pytest.mark.parametrize(("stored", "shown"), [
    ("012345678901000", "01.234.567.8-901.000"),  # 15 digits: the dotted DJP format
    ("0123456789012345", "0123 4567 8901 2345"),  # 16 digits (NIK / 0 + old number): groups of four
    ("01.234.567.8-901.000", "01.234.567.8-901.000"),  # already punctuated input
    ("12345", "12345"),  # unexpected length: as stored
])
def test_npwp_display(stored, shown):
    assert format_npwp(stored) == shown
