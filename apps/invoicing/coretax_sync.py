"""Sending an organization's Coretax files through its provider, and applying the results:
the Faktur Pajak numbers Coretax issues are recorded on the invoices (once, as by hand)."""

import logging

from django.db import transaction
from django.utils.translation import gettext as _

from apps.core.crypto import decrypt, encrypt
from apps.core.exceptions import InvalidStateTransition, ValidationFailed

from . import coretax
from .coretax_api import (
    WITHHOLDING_TYPES, ConnectorName, CoretaxConnector, CoretaxError, DocumentType, Result, SubmissionStatus, build_connector,
)
from .models import CoretaxConnection, CoretaxSubmission
from .services import record_tax_invoice_number

logger = logging.getLogger(__name__)
FILE_LABELS = {kind: kind.upper() for kind in WITHHOLDING_TYPES}


def save_connection(*, organization, connector: str, api_url: str = "", token: str = "", is_active: bool = True) -> CoretaxConnection:
    """Set up (or change) the organization's connection; an empty token keeps the stored one."""
    if connector not in ConnectorName.values:
        raise ValidationFailed(_("Choose a connection."))
    connection = CoretaxConnection.objects.filter(organization=organization).first() or CoretaxConnection(organization=organization)
    connection.connector, connection.api_url, connection.is_active = connector, api_url.strip(), is_active
    if token:
        connection.token = encrypt(token.encode())
    if connector == ConnectorName.HTTP and is_active and (not connection.api_url.startswith("https://") or not connection.token):
        raise ValidationFailed(_("The Coretax connection needs an HTTPS address and an access token."))
    connection.save()
    return connection


def connector_for(organization) -> CoretaxConnector | None:
    connection = CoretaxConnection.objects.filter(organization=organization, is_active=True).first()
    if connection is None:
        return None
    token = decrypt(connection.token).decode() if connection.token else ""
    return build_connector(connection.connector, url=connection.api_url, token=token)


def _connection(organization) -> tuple[CoretaxConnector, str]:
    connection = CoretaxConnection.objects.filter(organization=organization, is_active=True).first()
    if connection is None:
        raise InvalidStateTransition(_("Connect the organization to Coretax first (Settings → Coretax connection)."))
    return connector_for(organization), connection.connector


def _submit(organization, connector, name, document_type, label, xml, file_name, created_by) -> CoretaxSubmission:
    receipt = connector.submit(document_type=document_type, tin=coretax.tin(organization.npwp), file_name=file_name, xml=xml)
    return CoretaxSubmission.objects.create(
        organization=organization, document_type=document_type, label=label[:100], connector=name,
        reference=receipt.reference, status=receipt.status, created_by=created_by,
    )


@transaction.atomic
def submit_invoices(*, organization, invoices, label: str, created_by=None) -> CoretaxSubmission:
    """Send sales invoices as one Faktur Pajak Keluaran file. Invoices that can't be reported or
    already have their faktur number are left out."""
    connector, name = _connection(organization)
    invoices = [i for i in invoices if not coretax.invoice_problem(i) and not i.tax_invoice_number]
    if not invoices:
        raise ValidationFailed(_("None of these invoices can be sent: they have their Faktur Pajak number already, or can't be reported."))
    xml = coretax.build_invoices_xml(organization, invoices)
    submission = _submit(
        organization, connector, name, DocumentType.TAX_INVOICES, label, xml,
        coretax.filename(organization, label.replace("/", "-"), kind="faktur-pajak-keluaran"), created_by,
    )
    submission.invoices.set(invoices)
    return refresh_submission(submission)


@transaction.atomic
def submit_returns(*, organization, notes, label: str, created_by=None) -> CoretaxSubmission:
    connector, name = _connection(organization)
    notes = [n for n in notes if not coretax.exportable_problem(n)]
    if not notes:
        raise ValidationFailed(_("None of these returns can be sent to Coretax."))
    xml = coretax.build_xml(organization, notes)
    submission = _submit(organization, connector, name, DocumentType.INPUT_RETURNS, label, xml,
                         coretax.filename(organization, label.replace("/", "-")), created_by)
    submission.returns.set(notes)
    return refresh_submission(submission)


@transaction.atomic
def submit_withholdings(*, organization, year: int, month: int, document_type: str, created_by=None) -> CoretaxSubmission:
    """Send a month's eBupot file (``taxes.ebupot``); the bukti potong numbers that come back are
    recorded on the payslips and payments."""
    from apps.taxes import ebupot  # taxes reads invoicing

    connector, name = _connection(organization)
    batch = ebupot.month_batches(organization, year, month).get(document_type)
    if batch is None or not batch.records:
        raise ValidationFailed(_("There is nothing to send in this file."))
    if batch.problems:
        raise ValidationFailed(_("Fix these first: %(problems)s") % {"problems": " ".join(batch.problems)})
    label = f"{FILE_LABELS.get(document_type, document_type)} {month:02d}/{year}"
    submission = _submit(
        organization, connector, name, document_type, label, batch.xml(organization),
        ebupot.file_name(organization, document_type, year, month), created_by,
    )
    submission.records = [[r.source, r.pk] for r in batch.records]
    submission.save(update_fields=["records", "updated_at"])
    return refresh_submission(submission)


@transaction.atomic
def apply_result(submission: CoretaxSubmission, result: Result) -> CoretaxSubmission:
    """The outcome and the numbers it brings, together: a submission is never marked done with only
    some of its numbers recorded (each number is recorded once, so applying it again is harmless)."""
    submission.status = result.status
    submission.results = [{"reference": i.reference, "number": i.number, "error": i.error} for i in result.items]
    submission.save(update_fields=["status", "results", "updated_at"])
    if not submission.organization_id and submission.document_type == DocumentType.TAX_INVOICES:
        from apps.billing.coretax import record_numbers  # the platform's own subscription fakturs

        record_numbers(result.items)
    if submission.organization_id and submission.document_type in WITHHOLDING_TYPES:
        from apps.taxes.ebupot import record_numbers

        record_numbers(submission, result.items)
    if submission.organization_id and submission.document_type == DocumentType.TAX_INVOICES:
        invoices = {i.number: i for i in submission.invoices.all()}
        for item in result.items:
            invoice = invoices.get(item.reference)
            if invoice is None or not item.number or invoice.tax_invoice_number:
                continue
            try:
                record_tax_invoice_number(invoice=invoice, number=item.number)
            except (ValidationFailed, InvalidStateTransition) as exc:
                logger.warning("Faktur number %s for %s not recorded: %s", item.number, invoice.number, exc)
    return submission


def refresh_submission(submission: CoretaxSubmission) -> CoretaxSubmission:
    """Ask the provider for the outcome and record the faktur numbers issued."""
    if submission.status != SubmissionStatus.PENDING:
        return submission
    connector = connector_for(submission.organization) if submission.organization_id else platform_connector()
    if connector is None:
        return submission
    return apply_result(submission, connector.fetch(submission.reference))


def refresh_pending() -> int:
    """Nightly: bring every pending submission up to date. Returns how many were settled."""
    settled = 0
    for submission in CoretaxSubmission.objects.filter(status=SubmissionStatus.PENDING).select_related("organization"):
        try:
            settled += refresh_submission(submission).status != SubmissionStatus.PENDING
        except CoretaxError as exc:
            logger.warning("Coretax submission %s not refreshed: %s", submission.pk, exc)
    return settled


def platform_connector() -> CoretaxConnector | None:
    """The platform's own connection (``CORETAX_CONNECTOR`` etc.), for subscription fakturs."""
    from django.conf import settings

    if not settings.CORETAX_CONNECTOR:
        return None
    return build_connector(settings.CORETAX_CONNECTOR, url=settings.CORETAX_API_URL, token=settings.CORETAX_API_TOKEN)
