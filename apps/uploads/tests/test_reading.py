"""Reading uploaded files (pure)."""

import io
from datetime import date
from decimal import Decimal

import pytest
from openpyxl import Workbook

from apps.core.exceptions import ValidationFailed
from apps.uploads.domain import (
    Column,
    parse_amount,
    parse_bool,
    parse_date,
    parse_rate,
    read_table,
)

COLUMNS = (Column("name", "name", ("nama",), required=True), Column("amount", "amount", ("jumlah",)))


@pytest.mark.parametrize("text, amount", [
    ("1500", Decimal("1500")), ("1.500", Decimal("1500")), ("1,500", Decimal("1500")), ("1.234.567,89", Decimal("1234567.89")),
    ("1,234,567.89", Decimal("1234567.89")), ("Rp 1.500.000", Decimal("1500000")), ("1500.5", Decimal("1500.5")),
    ("12,5", Decimal("12.5")), ("", None),
])
def test_amounts_are_read_as_written(text, amount):
    assert parse_amount(text) == amount


def test_negative_amounts_only_where_allowed():
    assert parse_amount("(1.500)", allow_negative=True) == Decimal("-1500")
    assert parse_amount("-1500", allow_negative=True) == Decimal("-1500")
    for text in ("-1500", "abc", "1.5.0,0,0x", "=1+1"):
        with pytest.raises(ValueError):
            parse_amount(text)


@pytest.mark.parametrize("text, expected", [
    ("2026-01-31", date(2026, 1, 31)), ("31/01/2026", date(2026, 1, 31)), ("31-1-2026", date(2026, 1, 31)),
    ("31 Jan 2026", date(2026, 1, 31)), ("17 Agustus 2026", date(2026, 8, 17)), ("2026-01-31 00:00:00", date(2026, 1, 31)),
])
def test_dates_are_read_day_first(text, expected):
    assert parse_date(text) == expected


def test_unreadable_values_are_refused():
    for text in ("31/13/2026", "2026/01/31", "tomorrow"):
        with pytest.raises(ValueError):
            parse_date(text)
    with pytest.raises(ValueError):
        parse_bool("maybe")
    assert parse_bool("Ya") and not parse_bool("tidak") and parse_rate("1,5%") == Decimal("1.5")


def test_the_header_is_found_below_title_lines_in_any_language_and_order():
    text = "Daftar pelanggan\nper 31/12/2025\n\nJUMLAH;Nama;Lain\n1.500;PT A;x\n;;\n2.000;PT B;y\n"
    table = read_table(text.encode(), "list.csv", COLUMNS)
    assert [(r.number, r.values) for r in table.rows] == [
        (5, {"name": "PT A", "amount": "1.500"}), (7, {"name": "PT B", "amount": "2.000"}),
    ]
    assert table.found == {"name", "amount"} and table.missing == []


def test_a_missing_required_column_is_reported():
    table = read_table(b"amount\n1\n", "a.csv", COLUMNS)
    assert table.missing == ["name"]


def test_cells_are_sanitized_and_formula_guards_removed():
    text = "name\n'=PT A\u200b\nPT\x07 B\n".encode()
    assert [r.get("name") for r in read_table(text, "a.csv", COLUMNS).rows] == ["=PT A", "PT B"]


def test_windows_csv_and_xlsx_are_read():
    assert read_table("nama\nKoperasi Sejahtera ©\n".encode("cp1252"), "a.csv", COLUMNS).rows[0].get("name") == "Koperasi Sejahtera ©"
    book = Workbook()
    sheet = book.active
    sheet.append(["Laporan"])
    sheet.append(["Nama", "Jumlah"])
    sheet.append(["PT A", 1500.5])
    sheet.append(["PT B", 2000])
    buffer = io.BytesIO()
    book.save(buffer)
    rows = read_table(buffer.getvalue(), "a.xlsx", COLUMNS).rows
    assert [(r.get("name"), r.get("amount")) for r in rows] == [("PT A", "1500.5"), ("PT B", "2000")]


def test_files_that_are_not_tables_are_refused():
    for content, name in [(b"", "a.csv"), (b"x" * (2 * 1024 * 1024 + 1), "a.csv"), (b"name\n", "a.csv"), (b"PK\x03\x04junk", "a.xlsx"),
                          (b"name\nx\n", "a.pdf"), (b"\x00\x01binary", "a.csv")]:
        with pytest.raises(ValidationFailed):
            read_table(content, name, COLUMNS)
