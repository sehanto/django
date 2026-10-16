"""What every upload does: read the file, check each row with the same forms and services as the
pages, and either show the result (a check, rolled back) or apply it (all rows or none).

Importers are the delivery layer, like a form: they turn a row into the input of a service
(``contacts.services.create_contact``, ``invoicing.services.record_tax_invoice_number`` ...) and
never write models themselves, so every rule of the pages (validation, tenancy, the database
guards, the audit trail) holds for an uploaded row too.
"""

import csv
import io
from collections.abc import Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field

from django import forms
from django.core.exceptions import ValidationError
from django.db import DatabaseError, IntegrityError, transaction
from django.utils import translation
from django.utils.translation import gettext as _

from apps.core.exceptions import DomainError
from apps.core.sanitizers import csv_safe

from ..domain import (
    Column,
    Report,
    Row,
    RowOutcome,
    Table,
    parse_amount,
    parse_bool,
    parse_choice,
    parse_date,
    read_table,
)
from ..services import PendingFile, ensure_not_applied, record_upload


class RowError(Exception):
    """A row that can't be applied; the message says why."""


def labels(choices) -> list[tuple[str, set[str]]]:
    """(value, its labels in English and Indonesian) for ``parse_choice``."""
    result = []
    for value, label in choices:
        names = set()
        for language in ("en", "id"):
            with translation.override(language):
                names.add(str(label))
        result.append((value, names))
    return result


def form_errors(form: forms.BaseForm) -> str:
    messages = []
    for name, errors in form.errors.items():
        field = form.fields.get(name)
        prefix = f"{field.label}: " if field is not None and name != "__all__" else ""
        messages += [f"{prefix}{error}" for error in errors]
    return " ".join(messages)


@dataclass
class Context:
    """Who uploads, for which organization, with what options (from the importer's options form)."""

    organization: object
    user: object
    options: dict = field(default_factory=dict)
    request: object = None


class Importer:
    kind: str = ""
    title = ""
    description = ""
    group = ""
    columns: Sequence[Column] = ()
    example: Sequence[dict] = ()  # rows of the downloadable template
    options_form: type[forms.Form] | None = None
    feature: str | None = None  # a plan feature the upload needs

    # --- Template ---------------------------------------------------------------

    def template_csv(self) -> str:
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow([column.label for column in self.columns])
        for row in self.example:
            writer.writerow([csv_safe(row.get(column.key, "")) for column in self.columns])
        return buffer.getvalue()

    # --- Rows -------------------------------------------------------------------

    def prepare(self, context: Context, table: Table, report: Report) -> None:
        """Before the rows: checks of the file as a whole (add them to ``report.problems``)."""

    def apply_row(self, context: Context, row: Row) -> tuple[str, str, str]:
        """Apply one row: (outcome, label, message). Raise RowError, DomainError or ValidationError."""
        raise NotImplementedError

    def finish(self, context: Context, table: Table, report: Report) -> None:
        """After the rows (e.g. post the entry they add up to, or list what the file didn't mention)."""

    def run(self, context: Context, table: Table) -> Report:
        report = Report()
        for label in table.missing:
            report.problems.append(_("The file has no column %(column)s.") % {"column": label})
        if report.problems:
            return report
        self.prepare(context, table, report)
        if report.problems:
            return report
        for row in table.rows:
            try:
                with transaction.atomic():  # a refused row leaves nothing behind
                    outcome, label, message = self.apply_row(context, row)
            except (RowError, DomainError) as exc:
                report.add(row.number, RowOutcome.ERROR, self.row_label(row), str(getattr(exc, "message", exc)))
            except ValidationError as exc:
                report.add(row.number, RowOutcome.ERROR, self.row_label(row), " ".join(exc.messages))
            except IntegrityError:
                report.add(row.number, RowOutcome.ERROR, self.row_label(row), _("The database refused this row."))
            else:
                report.add(row.number, outcome, label, message)
        if report.ok:
            try:
                with transaction.atomic():
                    self.finish(context, table, report)
            except DomainError as exc:
                report.problems.append(exc.message)
        return report

    def row_label(self, row: Row) -> str:
        return ""

    # --- Value helpers ------------------------------------------------------------

    @staticmethod
    def amount(row: Row, key: str, label: str, *, required: bool = False, allow_negative: bool = False):
        try:
            value = parse_amount(row.get(key), allow_negative=allow_negative)
        except ValueError:
            raise RowError(_("%(column)s: %(value)s is not an amount.") % {"column": label, "value": row.get(key)}) from None
        if value is None and required:
            raise RowError(_("%(column)s: enter an amount.") % {"column": label})
        return value

    @staticmethod
    def date(row: Row, key: str, label: str, *, required: bool = False):
        try:
            value = parse_date(row.get(key))
        except ValueError:
            raise RowError(_("%(column)s: %(value)s is not a date (use DD/MM/YYYY).") % {"column": label, "value": row.get(key)}) from None
        if value is None and required:
            raise RowError(_("%(column)s: enter a date.") % {"column": label})
        return value

    @staticmethod
    def flag(row: Row, key: str, label: str, *, default: bool = False) -> bool:
        try:
            return parse_bool(row.get(key), default=default)
        except ValueError:
            raise RowError(_("%(column)s: write yes or no.") % {"column": label}) from None

    @staticmethod
    def choice(row: Row, key: str, label: str, choices, *, default: str = "") -> str:
        text = row.get(key)
        if not text:
            return default
        value = parse_choice(text, choices)
        if value is None:
            raise RowError(_("%(column)s: %(value)s is not one of the choices.") % {"column": label, "value": text})
        return value


# --- Check and apply ----------------------------------------------------------------


def _table(importer: Importer, pending: PendingFile) -> Table:
    return read_table(pending.content, pending.name, importer.columns)


@contextmanager
def _rolled_back():
    with transaction.atomic():
        yield
        transaction.set_rollback(True)


def check(importer: Importer, context: Context, pending: PendingFile) -> Report:
    """What applying the file would do, without keeping any of it."""
    table = _table(importer, pending)
    try:
        with _rolled_back():
            ensure_not_applied(context.organization, importer.kind, pending.sha256)
            return importer.run(context, table)
    except DatabaseError:
        report = Report()
        report.problems.append(_("The file couldn't be checked. Try again."))
        return report


def apply(importer: Importer, context: Context, pending: PendingFile) -> Report:
    """Apply every row, or none if any is refused (the same rules as the check)."""
    table = _table(importer, pending)
    with transaction.atomic():
        report = importer.run(context, table)
        if not report.ok:
            transaction.set_rollback(True)
            return report
        record_upload(organization=context.organization, pending=pending, report=report, created_by=context.user, request=context.request)
    return report
