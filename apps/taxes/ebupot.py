"""Coretax eBupot import files (bukti potong) for a month, following DJP's converter templates
("BPMP Excel to XML v.3", "BP21 v.4", "BP26", "BPA1", "BPPU v.3" and their bp*.zip samples):

* BPMP: permanent employees' PPh 21 in January to November (monthly TER);
* BPA1: their 1721-A1, in the last period (December, or the month they leave);
* BP21: non-permanent workers (monthly TER, or one record per day for daily workers),
  severance, and PPh 21 on fees to non-employees paid with a bill;
* BP26: non-resident employees' PPh 26;
* BPPU: PPh 23 and 4(2) withheld from suppliers;
* BPNR: PPh 26 withheld from suppliers abroad ("BPNR Excel to XML v.2");
* BPSP: final PPh deposited on one's own income ("BPSP Excel to XML v.3").

Coretax computes each record's tax from gross x deemed % x rate (progressive codes take the
top bracket rate and are computed by Coretax), so the amounts match what was withheld here.
Records that already have their bukti potong number are left out, so a file is never sent twice.
Element order follows the schemas; keep it.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from xml.etree import ElementTree as ET

from django.utils.translation import gettext as _

from apps.contacts.validators import HEAD_OFFICE, npwp16
from apps.core.money import ZERO
from apps.invoicing.coretax_api import DocumentType

NIL = object()
XSI = "http://www.w3.org/2001/XMLSchema-instance"
ROOTS = {  # root, list, record
    DocumentType.BPMP: ("MmPayrollBulk", "ListOfMmPayroll", "MmPayroll"),
    DocumentType.BP21: ("Bp21Bulk", "ListOfBp21", "Bp21"),
    DocumentType.BP26: ("BP26Bulk", "ListOfBP26", "BP26"),
    DocumentType.BPA1: ("A1Bulk", "ListOfA1", "A1"),
    DocumentType.BPPU: ("BpuBulk", "ListOfBpu", "Bpu"),
    DocumentType.BPNR: ("BPNRBulk", "ListOfBPNR", "BPNR"),
    DocumentType.BPSP: ("SelfPaymentBulk", "ListOfSelfPayment", "SelfPayment"),
}
FILE_NAMES = {
    DocumentType.BPMP: "bpmp", DocumentType.BP21: "bp21", DocumentType.BP26: "bp26", DocumentType.BPA1: "bpa1",
    DocumentType.BPPU: "bppu", DocumentType.BPNR: "bpnr", DocumentType.BPSP: "bpsp",
}
# Kode objek pajak (DJP's REF sheets).
PERMANENT, NON_PERMANENT_MONTHLY, DAILY, DAILY_HIGH = "21-100-01", "21-100-35", "21-100-24", "21-100-30"
SEVERANCE, PPH_26 = "21-401-01", "27-100-99"
LATE_SEVERANCE = "21-100-25"  # severance paid from the third calendar year on: Art. 17 rates
PAYROLL_DOCUMENT, BILL_DOCUMENT = "PaymentProof", "CommercialInvoice"  # Bukti Pembayaran, Surat Tagihan
PAYSLIP, PAYMENT, SELF_DEPOSIT = "payslip", "payment", "self_deposit"


@dataclass
class Record:
    source: str  # PAYSLIP or PAYMENT: whose bukti potong number it is
    pk: int
    fields: list[tuple[str, object]]


@dataclass
class Batch:
    document_type: str
    records: list[Record] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        return DocumentType(self.document_type).label

    def xml(self, organization) -> bytes:
        root_tag, list_tag, record_tag = ROOTS[self.document_type]
        root = ET.Element(root_tag, {"xmlns:xsi": XSI})
        ET.SubElement(root, "TIN").text = npwp16(organization.npwp)
        listing = ET.SubElement(root, list_tag)
        for record in self.records:
            element = ET.SubElement(listing, record_tag)
            for tag, value in record.fields:
                child = ET.SubElement(element, tag)
                if value is NIL:
                    child.set("xsi:nil", "true")
                else:
                    child.text = _value(value)
        ET.indent(root, space="\t")
        return b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n' + ET.tostring(root, encoding="utf-8")


def _value(value) -> str:
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return f"{value.normalize():f}" if value else "0"
    return str(value)


def file_name(organization, document_type: str, year: int, month: int) -> str:
    return f"{FILE_NAMES[document_type]}-{npwp16(organization.npwp)}-{year}-{month:02d}.xml"


def _nitku(tin: str) -> str:
    return tin + HEAD_OFFICE


def _period(year: int, month: int) -> list[tuple[str, object]]:
    return [("TaxPeriodMonth", month), ("TaxPeriodYear", year)]


def _payroll_document(run) -> list[tuple[str, object]]:
    return [("Document", PAYROLL_DOCUMENT), ("DocumentNumber", run.journal_entry.number), ("DocumentDate", run.journal_entry.date)]


def month_batches(organization, year: int, month: int) -> dict[str, Batch]:
    """The month's eBupot files, by document type (only those with records or problems)."""
    from apps.payroll.models import PayrollRun

    batches = {kind: Batch(kind) for kind in ROOTS}
    if not npwp16(organization.npwp):
        for batch in batches.values():
            batch.problems.append(_("Enter the organization's NPWP first."))
        return batches
    office = _nitku(npwp16(organization.npwp))
    run = (
        PayrollRun.objects.for_organization(organization).filter(year=year, month=month, status=PayrollRun.Status.POSTED)
        .select_related("journal_entry").first()
    )
    if run is not None:
        _payroll(batches, run, office)
    _payments(batches, organization, year, month, office)
    _self_deposits(batches, organization, year, month, office)
    return {kind: batch for kind, batch in batches.items() if batch.records or batch.problems}


def _payroll(batches: dict[str, Batch], run, office: str) -> None:
    from apps.payroll.domain import TaxScheme

    for slip in run.payslips.select_related("employee").order_by("employee__name", "id"):
        if slip.withholding_slip:
            continue
        if slip.tax_scheme == TaxScheme.PPH_26:
            _pph26_slip(batches[DocumentType.BP26], slip, run, office)
            continue
        tin = slip.employee.tin  # citizens always have a NIK; a foreign resident may only have a passport
        _pph21_slip(batches, slip, run, office, tin)
        if slip.severance and tin:
            _severance_slip(batches[DocumentType.BP21], slip, run, office, tin)


def _pph26_slip(batch: Batch, slip, run, office: str) -> None:
    e = slip.employee
    tin = e.foreign_tin or e.passport_number
    if not tin:
        batch.problems.append(_("%(name)s: enter their foreign tax ID or passport number.") % {"name": e.name})
        return
    batch.records.append(Record(PAYSLIP, slip.pk, [
        *_period(run.year, run.month), ("CounterpartTin", tin), ("CounterpartReceiptNumber", e.treaty_document or "-"),
        ("CounterpartName", e.name), ("CounterpartCountry", e.nationality), ("CounterpartAddress", e.full_address or NIL),
        ("CounterpartDob", NIL), ("CounterpartBirthCity", NIL), ("CounterpartPassport", e.passport_number or NIL),
        ("CounterpartKitas", NIL), ("TaxCertificate", "COD" if slip.pph26_rate is not None and e.treaty_document else "N/A"),
        ("TaxObjectCode", PPH_26), ("Gross", slip.taxable_gross + slip.severance), ("Deemed", 100),
        ("Rate", slip.pph26_rate if slip.pph26_rate is not None else Decimal("20")), *_payroll_document(run),
        ("IDPlaceOfBusinessActivity", office), ("WithholdingDate", run.journal_entry.date),
    ]))


def _pph21_slip(batches: dict[str, Batch], slip, run, office: str, tin: str) -> None:
    """A resident's monthly PPh 21: BPMP for permanent employees, the 1721-A1 at the year's
    end (BPA1), BP21 for non-permanent and daily workers."""
    from apps.payroll.domain import EmployeeKind, TaxScheme, article_17_bracket, daily_rows
    from apps.payroll.selectors import annual_statement

    e, year, month, on = slip.employee, run.year, run.month, run.journal_entry.date
    if slip.tax_scheme == TaxScheme.TER_MONTHLY and e.kind == EmployeeKind.PERMANENT:
        batches[DocumentType.BPMP].records.append(Record(PAYSLIP, slip.pk, [
            *_period(year, month), ("CounterpartOpt", "Resident" if e.is_citizen else "Foreign"),
            ("CounterpartPassport", NIL if e.is_citizen or not e.passport_number else e.passport_number),
            ("CounterpartTin", tin or NIL), ("StatusTaxExemption", slip.ptkp_status), ("Position", e.position or "Pegawai"),
            ("TaxCertificate", "N/A"), ("TaxObjectCode", PERMANENT), ("Gross", slip.taxable_gross), ("Rate", slip.ter_rate),
            ("IDPlaceOfBusinessActivity", office), ("WithholdingDate", on),
        ]))
    elif slip.tax_scheme == TaxScheme.ANNUAL:
        batch = batches[DocumentType.BPA1]
        if not tin:
            batch.problems.append(_("%(name)s: enter their NPWP.") % {"name": e.name})
        else:
            batch.records.append(Record(PAYSLIP, slip.pk, _a1_fields(annual_statement(e, year), e, tin, office, on)))
    elif slip.tax_scheme in (TaxScheme.TER_MONTHLY, TaxScheme.TER_DAILY, TaxScheme.DAILY_ARTICLE_17):
        batch = batches[DocumentType.BP21]
        if not tin:
            batch.problems.append(_("%(name)s: enter their NIK or NPWP.") % {"name": e.name})
            return
        if slip.tax_scheme == TaxScheme.TER_MONTHLY:
            rows = [(NON_PERMANENT_MONTHLY, slip.taxable_gross, 100, slip.ter_rate)]
        else:
            rows = [
                (DAILY, row.gross, 100, row.rate) if row.rate is not None
                else (DAILY_HIGH, row.gross, 50, article_17_bracket(row.gross / 2))
                for row in daily_rows(slip.taxable_gross, slip.days_worked)
            ]
        for code, gross, deemed, rate in rows:
            batch.records.append(Record(PAYSLIP, slip.pk, _bp21_fields(year, month, tin, slip.ptkp_status, code, gross, deemed, rate,
                                                                        _payroll_document(run), office, on)))


def _severance_slip(batch: Batch, slip, run, office: str, tin: str) -> None:
    """Severance (PP 68/2009): final within two calendar years of its first payment, Art. 17 after."""
    from apps.payroll.domain import article_17_bracket, severance_bracket

    code, rate = (
        (SEVERANCE, severance_bracket(slip.severance)) if slip.severance_final
        else (LATE_SEVERANCE, article_17_bracket(slip.severance))
    )
    batch.records.append(Record(PAYSLIP, slip.pk, _bp21_fields(
        run.year, run.month, tin, slip.ptkp_status, code, slip.severance, 100, rate, _payroll_document(run), office, run.journal_entry.date,
    )))


def _bp21_fields(year, month, tin, ptkp, code, gross, deemed, rate, document, office, on) -> list[tuple[str, object]]:
    return [
        *_period(year, month), ("CounterpartTin", tin), ("IDPlaceOfBusinessActivityOfIncomeRecipient", _nitku(tin)),
        ("StatusTaxExemption", ptkp), ("TaxCertificate", "N/A"), ("TaxObjectCode", code), ("Gross", gross), ("Deemed", deemed),
        ("Rate", rate), *document, ("IDPlaceOfBusinessActivity", office), ("WithholdingDate", on),
    ]


def _a1_fields(st, employee, tin: str, office: str, on: date) -> list[tuple[str, object]]:
    from apps.payroll.models import PriorIncome

    prior = PriorIncome.objects.filter(employee=employee, year=st.year).exclude(slip_number="").order_by("-last_month").first()
    if st.first_month == 1 and st.last_month == 12:
        status = "FullYear"
    else:
        status = "Annualized" if st.annual.annualized else "PartialYear"
    return [
        ("WorkForSecondEmployer", "Yes" if prior else "No"), ("TaxPeriodMonthStart", st.first_month),
        ("TaxPeriodMonthEnd", st.last_month), ("TaxPeriodYear", st.year),
        ("CounterpartOpt", "Resident" if employee.is_citizen else "Foreign"),
        ("CounterpartPassport", NIL if employee.is_citizen or not employee.passport_number else employee.passport_number),
        ("CounterpartTin", tin), ("TaxExemptOpt", st.final_slip.ptkp_status), ("StatusOfWithholding", status),
        ("CounterpartPosition", employee.position or "Pegawai"), ("TaxObjectCode", PERMANENT),
        ("NumberOfMonths", st.annual.months if status == "Annualized" else 0), ("SalaryPensionJhtTht", st.salary),
        # The tax allowance is already solved here, so Coretax shouldn't gross up again.
        ("GrossUpOpt", "No"), ("IncomeTaxBenefit", st.tax_allowance), ("OtherBenefit", st.allowances), ("Honorarium", ZERO),
        ("InsurancePaidByEmployer", st.premiums), ("Natura", st.benefits_in_kind), ("TantiemBonusThr", st.bonus),
        ("PensionContributionJhtThtFee", st.annual.contributions), ("Zakat", st.annual.zakat),
        ("PrevWhTaxSlip", prior.slip_number if prior else NIL), ("TaxCertificate", "N/A"), ("Article21IncomeTax", ZERO),
        ("IDPlaceOfBusinessActivity", office), ("WithholdingDate", on),
    ]


def _payments(batches: dict[str, Batch], organization, year: int, month: int, office: str) -> None:
    from apps.contacts.models import BuyerIdType
    from apps.invoicing.models import IncomeTaxWithheld
    from apps.payroll.domain import NON_EMPLOYEE_BASE, article_17_bracket

    from .selectors import withholdings

    for p in withholdings(organization, year, month):
        if p.withholding_slip:
            continue
        bill = p.invoice
        pph21 = p.income_tax_kind == IncomeTaxWithheld.PPH_21
        if p.income_tax_kind == IncomeTaxWithheld.PPH_26:
            _bpnr(batches[DocumentType.BPNR], p, year, month, office)
            continue
        batch = batches[DocumentType.BP21 if pph21 else DocumentType.BPPU]
        tin = bill.party_tin or (bill.party_id_number if bill.party_id_type == BuyerIdType.NATIONAL_ID else "")
        if not p.income_tax_object:
            batch.problems.append(_("Payment %(number)s: choose its tax object code.") % {"number": p.number})
            continue
        if not tin:
            batch.problems.append(_("Payment %(number)s: the supplier has no NPWP or NIK.") % {"number": p.number})
            continue
        document = [("Document", BILL_DOCUMENT), ("DocumentNumber", bill.number), ("DocumentDate", bill.date)]
        if pph21:
            rate = article_17_bracket(p.income_tax_base * NON_EMPLOYEE_BASE / 100)
            batch.records.append(Record(PAYMENT, p.pk, _bp21_fields(
                year, month, tin, "TK/0", p.income_tax_object, p.income_tax_base, 50, rate, document, office, p.date,
            )))
            continue
        batch.records.append(Record(PAYMENT, p.pk, [
            *_period(year, month), ("CounterpartTin", tin), ("IDPlaceOfBusinessActivityOfIncomeRecipient", bill.party_nitku or _nitku(tin)),
            ("TaxCertificate", "N/A"), ("TaxObjectCode", p.income_tax_object), ("TaxBase", p.income_tax_base),
            ("Rate", p.income_tax_rate), *document, ("IDPlaceOfBusinessActivity", office), ("GovTreasurerOpt", "N/A"),
            ("SP2DNumber", NIL), ("WithholdingDate", p.date),
        ]))


def _bpnr(batch: Batch, p, year: int, month: int, office: str) -> None:
    from .tax_objects import deemed, reference_rate

    bill, contact = p.invoice, p.invoice.contact
    tin = contact.foreign_tin
    if not tin:
        batch.problems.append(_("Payment %(number)s: enter the supplier's foreign tax ID on the contact.") % {"number": p.number})
        return
    treaty = p.income_tax_rate is not None and p.income_tax_rate < (reference_rate(p.income_tax_kind, p.income_tax_object) or ZERO)
    batch.records.append(Record(PAYMENT, p.pk, [
        *_period(year, month), ("CounterpartTin", tin), ("CounterpartReceiptNumber", contact.treaty_document if treaty else NIL),
        ("CounterpartName", bill.party_name), ("CounterpartCountry", bill.party_country), ("CounterpartAddress", bill.contact_address or NIL),
        ("CounterpartDob", NIL), ("CounterpartBirthCity", NIL), ("CounterpartPassport", NIL), ("CounterpartKitas", NIL),
        ("TaxCertificate", "COD" if treaty else "N/A"), ("TaxObjectCode", p.income_tax_object), ("GrossIncome", p.income_tax_base),
        ("Deemed", deemed(p.income_tax_kind, p.income_tax_object)), ("Rate", p.income_tax_rate),
        ("Document", BILL_DOCUMENT), ("DocumentNumber", bill.number), ("DocumentDate", bill.date), ("GovTreasurerOpt", "N/A"),
        ("SP2DNumber", NIL), ("WithholdingDate", p.date), ("IDPlaceOfBusinessActivity", office),
    ]))


def _self_deposits(batches: dict[str, Batch], organization, year: int, month: int, office: str) -> None:
    from .models import SelfDeposit

    batch = batches[DocumentType.BPSP]
    for d in SelfDeposit.objects.for_organization(organization).filter(
        year=year, month=month, status=SelfDeposit.Status.POSTED, slip_number="",
    ).order_by("document_date", "id"):
        batch.records.append(Record(SELF_DEPOSIT, d.pk, [
            *_period(year, month), ("TaxCertificate", "N/A"), ("TaxObjectCode", d.tax_object), ("TaxBase", d.tax_base),
            # Only for code 28-411-01 (foreign shipping and airlines).
            ("IncomeFromIndonesiaTaxBase", ZERO), ("IncomeFromIndonesiaIncomeTax", ZERO), ("IncomeFromForeignCountriesTaxBase", ZERO),
            ("IncomeFromForeignCountriesIncomeTax", ZERO), ("IncomeTaxArticle24CreditedIncomeTax", ZERO),
            ("IncomeTaxWithheldByOtherParty", ZERO), ("SelfPaymentIncomeTax", ZERO), ("Rate", d.rate),
            ("Document", BILL_DOCUMENT), ("DocumentNumber", d.document_number), ("DocumentDate", d.document_date),
            ("IDPlaceOfBusinessActivity", office), ("WithholdingDate", d.paid_on),
        ]))


def record_numbers(submission, items) -> None:
    """Record the bukti potong numbers Coretax issued, by the records' positions in the file
    (once each; a payslip with several records takes the first number)."""
    import logging

    from apps.core.exceptions import InvalidStateTransition, ValidationFailed
    from apps.invoicing.models import Payment
    from apps.invoicing.services import record_withholding_slip
    from apps.payroll.models import Payslip
    from apps.payroll.services import record_slip_number

    logger = logging.getLogger(__name__)
    records = submission.records
    for item in items:
        if not item.number or not item.reference.isdigit() or not 1 <= int(item.reference) <= len(records):
            continue
        source, pk = records[int(item.reference) - 1]
        try:
            if source == PAYSLIP:
                slip = Payslip.objects.filter(pk=pk, run__organization=submission.organization).first()
                if slip is not None and not slip.withholding_slip:
                    record_slip_number(payslip=slip, number=item.number)
            elif source == PAYMENT:
                payment = Payment.objects.for_organization(submission.organization).filter(pk=pk).first()
                if payment is not None and not payment.withholding_slip:
                    record_withholding_slip(payment=payment, number=item.number)
            elif source == SELF_DEPOSIT:
                from .models import SelfDeposit
                from .services import record_self_deposit_slip

                deposit = SelfDeposit.objects.for_organization(submission.organization).filter(pk=pk).first()
                if deposit is not None and not deposit.slip_number:
                    record_self_deposit_slip(deposit=deposit, number=item.number)
        except (ValidationFailed, InvalidStateTransition) as exc:
            logger.warning("Bukti potong %s for %s %s not recorded: %s", item.number, source, pk, exc)
