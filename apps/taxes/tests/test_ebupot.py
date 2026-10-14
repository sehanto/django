"""Coretax eBupot import files: element order as in DJP's converter schemas, the records each
month gets, and sending them through the Coretax connection."""

from datetime import date
from decimal import Decimal
from xml.etree import ElementTree as ET

import pytest
from django.urls import reverse
from django.utils import translation

from apps.core.exceptions import InvalidStateTransition, ValidationFailed
from apps.invoicing import coretax_sync
from apps.invoicing import services as invoicing
from apps.invoicing.coretax_api import DocumentType
from apps.invoicing.models import IncomeTaxWithheld, InvoiceKind
from apps.invoicing.services import IncomeTaxInput, InvoiceLineInput
from apps.payroll import services as payroll
from apps.payroll.models import Employee
from apps.payroll.services import PayInput
from apps.taxes import ebupot

D = Decimal
NIL = "{http://www.w3.org/2001/XMLSchema-instance}nil"
# The record elements of each schema, in order (xl/xmlMaps.xml of DJP's "Excel to XML" converters).
SEQUENCES = {
    DocumentType.BPMP: [
        "TaxPeriodMonth", "TaxPeriodYear", "CounterpartOpt", "CounterpartPassport", "CounterpartTin", "StatusTaxExemption",
        "Position", "TaxCertificate", "TaxObjectCode", "Gross", "Rate", "IDPlaceOfBusinessActivity", "WithholdingDate",
    ],
    DocumentType.BP21: [
        "TaxPeriodMonth", "TaxPeriodYear", "CounterpartTin", "IDPlaceOfBusinessActivityOfIncomeRecipient", "StatusTaxExemption",
        "TaxCertificate", "TaxObjectCode", "Gross", "Deemed", "Rate", "Document", "DocumentNumber", "DocumentDate",
        "IDPlaceOfBusinessActivity", "WithholdingDate",
    ],
    DocumentType.BP26: [
        "TaxPeriodMonth", "TaxPeriodYear", "CounterpartTin", "CounterpartReceiptNumber", "CounterpartName", "CounterpartCountry",
        "CounterpartAddress", "CounterpartDob", "CounterpartBirthCity", "CounterpartPassport", "CounterpartKitas", "TaxCertificate",
        "TaxObjectCode", "Gross", "Deemed", "Rate", "Document", "DocumentNumber", "DocumentDate", "IDPlaceOfBusinessActivity",
        "WithholdingDate",
    ],
    DocumentType.BPA1: [
        "WorkForSecondEmployer", "TaxPeriodMonthStart", "TaxPeriodMonthEnd", "TaxPeriodYear", "CounterpartOpt", "CounterpartPassport",
        "CounterpartTin", "TaxExemptOpt", "StatusOfWithholding", "CounterpartPosition", "TaxObjectCode", "NumberOfMonths",
        "SalaryPensionJhtTht", "GrossUpOpt", "IncomeTaxBenefit", "OtherBenefit", "Honorarium", "InsurancePaidByEmployer", "Natura",
        "TantiemBonusThr", "PensionContributionJhtThtFee", "Zakat", "PrevWhTaxSlip", "TaxCertificate", "Article21IncomeTax",
        "IDPlaceOfBusinessActivity", "WithholdingDate",
    ],
    DocumentType.BPPU: [
        "TaxPeriodMonth", "TaxPeriodYear", "CounterpartTin", "IDPlaceOfBusinessActivityOfIncomeRecipient", "TaxCertificate",
        "TaxObjectCode", "TaxBase", "Rate", "Document", "DocumentNumber", "DocumentDate", "IDPlaceOfBusinessActivity",
        "GovTreasurerOpt", "SP2DNumber", "WithholdingDate",
    ],
}


@pytest.fixture(autouse=True)
def english():
    with translation.override("en"):
        yield


@pytest.fixture
def employer(organization):
    organization.npwp = "0123456789012345"
    organization.save()
    return organization


def make(organization, **fields):
    defaults = dict(name="Budi", nik="3174061502560011", ptkp_status="TK/0", start_date=date(2025, 1, 1), base_salary=D("0"),
                    fixed_allowance=D("0"), bpjs_employment=False, bpjs_health=False, position="Kasir")
    return Employee.objects.create(organization=organization, **{**defaults, **fields})


def records(batch, organization):
    root = ET.fromstring(batch.xml(organization))
    assert root[0].tag == "TIN" and root[0].text == "0123456789012345"
    for record in root[1]:
        assert [child.tag for child in record] == SEQUENCES[batch.document_type]
    return [{child.tag: (None if child.get(NIL) else child.text) for child in record} for record in root[1]]


def test_a_month_of_payroll(employer):
    staff = make(employer)
    daily = make(employer, name="Harian", nik="3174061502560012", kind=Employee.Kind.NON_PERMANENT, pay_basis=Employee.PayBasis.DAILY)
    expat = make(employer, name="Smith", nationality="GBR", nik="", passport_number="P1", is_resident=False, foreign_tin="GB-123",
                 treaty_rate=D("0"), treaty_document="DGT-1")
    leaver = make(employer, name="Leaver", nik="3174061502560013", end_date=date(2026, 3, 31))
    for month in (1, 2):
        payroll.run_payroll(organization=employer, year=2026, month=month, pays=[PayInput(leaver, D("10000000"))])
    payroll.run_payroll(organization=employer, year=2026, month=3, pays=[
        PayInput(staff, D("10000000")), PayInput(daily, D("1350001"), days=3), PayInput(expat, D("40000000")),
        PayInput(leaver, D("10000000"), severance=D("60000000")),
    ])
    batches = ebupot.month_batches(employer, 2026, 3)
    assert set(batches) == {DocumentType.BPMP, DocumentType.BP21, DocumentType.BP26, DocumentType.BPA1}

    (bpmp,) = records(batches[DocumentType.BPMP], employer)
    assert (bpmp["CounterpartTin"], bpmp["TaxObjectCode"], bpmp["Gross"], bpmp["Rate"], bpmp["CounterpartPassport"]) == (
        "3174061502560011", "21-100-01", "10000000", "2", None)
    assert (bpmp["IDPlaceOfBusinessActivity"], bpmp["WithholdingDate"]) == ("0123456789012345000000", "2026-03-31")

    bp21 = records(batches[DocumentType.BP21], employer)
    # One per day: 450.000, 450.000 and 450.001 (the last above Rp450.000, at 0.5%), then the severance.
    assert [(r["TaxObjectCode"], r["Gross"], r["Rate"]) for r in bp21] == [
        ("21-100-24", "450000", "0"), ("21-100-24", "450000", "0"), ("21-100-24", "450001", "0.5"), ("21-401-01", "60000000", "5"),
    ]
    assert daily.payslips.get().pph21 == D("2250")  # what Coretax computes on those rows

    (bp26,) = records(batches[DocumentType.BP26], employer)
    assert (bp26["CounterpartTin"], bp26["TaxCertificate"], bp26["CounterpartReceiptNumber"], bp26["Rate"], bp26["CounterpartCountry"]) == (
        "GB-123", "COD", "DGT-1", "0", "GBR")

    (a1,) = records(batches[DocumentType.BPA1], employer)
    assert (a1["TaxPeriodMonthStart"], a1["TaxPeriodMonthEnd"], a1["StatusOfWithholding"], a1["SalaryPensionJhtTht"]) == (
        "1", "3", "PartialYear", "30000000")


def test_supplier_withholdings_and_their_codes(employer, supplier, account):
    supplier.npwp = "1091031210920664"
    supplier.save()
    bill = invoicing.issue_invoice(invoice=invoicing.create_invoice(
        organization=employer, kind=InvoiceKind.PURCHASE, contact=supplier, date=date(2026, 3, 1),
        lines=[InvoiceLineInput("Jasa teknik", account("6-1700"), D("1"), D("5000000"))],
    ))
    with pytest.raises(ValidationFailed, match="withheld at 2%"):
        invoicing.record_payment(invoice=bill, date=date(2026, 3, 10), amount=D("1000000"), cash_account=account("1-1100"),
                                 income_tax=IncomeTaxInput(IncomeTaxWithheld.PPH_23, base=D("1000000"), rate=D("3"), object_code="24-104-01"))
    with pytest.raises(ValidationFailed, match="tax object code of PPh 23"):
        invoicing.record_payment(invoice=bill, date=date(2026, 3, 10), amount=D("1000000"), cash_account=account("1-1100"),
                                 income_tax=IncomeTaxInput(IncomeTaxWithheld.PPH_23, base=D("1000000"), rate=D("10"), object_code="28-403-02"))
    payment = invoicing.record_payment(invoice=bill, date=date(2026, 3, 10), amount=D("5000000"), cash_account=account("1-1100"),
                                       income_tax=IncomeTaxInput(IncomeTaxWithheld.PPH_23, base=D("5000000"), rate=D("2")))
    batch = ebupot.month_batches(employer, 2026, 3)[DocumentType.BPPU]
    assert not batch.records and "choose its tax object code" in batch.problems[0]
    invoicing.record_tax_object(payment=payment, code="24-104-01")
    (row,) = records(ebupot.month_batches(employer, 2026, 3)[DocumentType.BPPU], employer)
    assert (row["CounterpartTin"], row["TaxObjectCode"], row["TaxBase"], row["Rate"], row["DocumentNumber"], row["SP2DNumber"]) == (
        "1091031210920664", "24-104-01", "5000000", "2", bill.number, None)


def test_sending_records_the_numbers(employer, auth_client):
    staff = make(employer)
    payroll.run_payroll(organization=employer, year=2026, month=3, pays=[PayInput(staff, D("10000000"))])
    with pytest.raises(InvalidStateTransition, match="Connect the organization"):
        coretax_sync.submit_withholdings(organization=employer, year=2026, month=3, document_type=DocumentType.BPMP)
    coretax_sync.save_connection(organization=employer, connector="fake")
    page = auth_client.get(reverse("taxes:withholding_month", args=[2026, 3]))
    assert page.status_code == 200 and [b.document_type for b in page.context["batches"]] == [DocumentType.BPMP]
    xml = auth_client.get(reverse("taxes:ebupot_xml", args=[2026, 3, "bpmp"]))
    assert xml.status_code == 200 and b"<MmPayrollBulk" in xml.content
    response = auth_client.post(reverse("taxes:ebupot_send", args=[2026, 3, "bpmp"]))
    assert response.status_code == 302
    slip = staff.payslips.get()
    assert slip.withholding_slip.startswith("BPMP")
    assert ebupot.month_batches(employer, 2026, 3) == {}  # numbered: nothing left to send
