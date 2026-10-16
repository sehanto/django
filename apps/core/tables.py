"""Tables to download, as CSV or Excel (XLSX), from a header row and data rows.

CSV is what Excel opens in either language (UTF-8 with a BOM, ``;``-separated); XLSX keeps
amounts as numbers and dates as dates, with the header frozen. Text a spreadsheet would run as a
formula is neutralised in both (OWASP CSV injection): ``csv_safe`` in CSV, a text cell in XLSX.
"""

import csv
import io
from collections.abc import Iterable
from datetime import date, datetime
from decimal import Decimal

from django.db import models
from django.http import HttpResponse

from .sanitizers import csv_safe

XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MAX_COLUMN_WIDTH = 60


class ExportFormat(models.TextChoices):
    CSV = "csv", "CSV"
    XLSX = "xlsx", "Excel (XLSX)"


def _csv(rows: Iterable[list]) -> bytes:
    buffer = io.StringIO()
    buffer.write("﻿")  # BOM so Excel opens UTF-8 correctly
    writer = csv.writer(buffer, delimiter=";")
    writer.writerows([csv_safe(cell) for cell in row] for row in rows)
    return buffer.getvalue().encode("utf-8")


def _xlsx(rows: Iterable[list], title: str) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font

    book = Workbook()
    sheet = book.active
    sheet.title = (title or "Sheet")[:31]
    widths: dict[int, int] = {}
    for row_number, row in enumerate(rows, start=1):
        for column, value in enumerate(row, start=1):
            if isinstance(value, datetime):
                value = value.replace(tzinfo=None)
            cell = sheet.cell(row=row_number, column=column, value=value if isinstance(value, (int, float, Decimal, date)) else
                              ("" if value is None else str(value)))
            if isinstance(cell.value, str):
                cell.data_type = "s"  # never a formula, whatever the text starts with
            elif isinstance(value, Decimal):
                cell.number_format = "#,##0.00" if value != value.to_integral() else "#,##0"
            elif isinstance(value, date):
                cell.number_format = "yyyy-mm-dd"
            if row_number == 1:
                cell.font = Font(bold=True)
            widths[column] = max(widths.get(column, 0), min(MAX_COLUMN_WIDTH, len(str(value if value is not None else "")) + 2))
    sheet.freeze_panes = "A2"
    for column, width in widths.items():
        sheet.column_dimensions[sheet.cell(row=1, column=column).column_letter].width = width
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def download(name: str, rows: Iterable[list], export_format: str, *, title: str = "") -> HttpResponse:
    """``name`` without its extension (``trial-balance``); ``rows`` start with the header."""
    export_format = ExportFormat(export_format)
    if export_format == ExportFormat.XLSX:
        response = HttpResponse(_xlsx(rows, title or name), content_type=XLSX_TYPE)
    else:
        response = HttpResponse(_csv(rows), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{name}.{export_format.value}"'
    return response
