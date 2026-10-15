"""The PPN deadlines the summary shows: the SPT Masa PPN and its deposit by the end of the next
month (PMK 81/2024), each faktur uploaded by the 15th of the next month (late: 1% of its DPP),
and a non-PKP's duty to register once turnover passes Rp4,8 miliar (PMK 197/2013)."""

from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse

from apps.invoicing import services
from apps.invoicing.tests.test_coretax import books, sale  # noqa: F401 - fixtures
from apps.ledger.services import JournalLineInput, create_journal_entry
from apps.taxes.domain import faktur_upload_due, pkp_threshold_month, vat_return_due

D = Decimal


@pytest.mark.parametrize(("year", "month", "due"), [
    (2026, 1, date(2026, 3, 2)),  # 28 February 2026 is a Saturday
    (2026, 11, date(2026, 12, 31)),
    (2026, 12, date(2027, 2, 1)),  # 31 January 2027 is a Sunday
])
def test_the_spt_masa_ppn_is_due_at_the_end_of_the_next_month(year, month, due):
    assert vat_return_due(year, month) == due


def test_a_faktur_is_uploaded_by_the_15th_of_the_next_month():
    assert faktur_upload_due(2026, 3) == date(2026, 4, 15) and faktur_upload_due(2026, 12) == date(2027, 1, 15)


def test_the_month_turnover_passes_the_pkp_threshold():
    assert pkp_threshold_month([D("2000000000"), D("2000000000"), D("1000000000"), D("1")]) == 3
    assert pkp_threshold_month([D("400000000")] * 12) is None  # exactly Rp4,8 miliar: only more than it counts
    assert pkp_threshold_month([D("400000000")] * 11 + [D("400000001")]) == 12


def vat_page(auth_client):
    return auth_client.get(reverse("reports:vat"), {"year": 2026})


def test_fakturs_without_their_number_are_flagged_after_the_15th(auth_client, books, sale):  # noqa: F811
    march = vat_page(auth_client).context["rows"][2]
    assert march["unnumbered"] == 1 and march["faktur_due"] == date(2026, 4, 15) and march["faktur_late"]
    assert march["return_due"] == date(2026, 4, 30)
    services.record_tax_invoice_number(invoice=sale, number="04002600000012345")
    assert vat_page(auth_client).context["rows"][2]["unnumbered"] == 0


def test_a_business_that_is_not_a_pkp_is_told_to_register(auth_client, organization, account):
    for month, amount in ((1, "3000000000"), (2, "2000000000")):
        create_journal_entry(organization=organization, date=date(2026, month, 10), description="Penjualan", lines=[
            JournalLineInput(account("1-1100"), debit=D(amount)), JournalLineInput(account("4-1000"), credit=D(amount))])
    page = vat_page(auth_client)
    assert page.context["pkp_by"] == date(2026, 3, 31) and b"alert-danger" in page.content  # passed in February
    organization.is_pkp = True
    organization.save(update_fields=["is_pkp"])
    assert "pkp_by" not in vat_page(auth_client).context
