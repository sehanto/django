"""Amounts paid or received in advance, recognised straight-line (SAK EMKM 14.13, 14.7), and
investments in joint ventures at cost (Bab 10)."""

from datetime import date
from decimal import Decimal

import pytest
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone, translation

from apps.core.exceptions import InvalidStateTransition, ValidationFailed
from apps.deferrals import services
from apps.deferrals.domain import DeferralKind, straight_line
from apps.deferrals.models import Deferral, DeferralPosting
from apps.invoicing import services as invoicing
from apps.invoicing.models import InvoiceKind
from apps.invoicing.services import InvoiceLineInput
from apps.ledger.models import JournalEntry
from apps.ledger.selectors import account_balances, verify_chain
from apps.ledger.services import JournalLineInput, create_journal_entry
from apps.reports.views import balance_sheet, income_statement

D = Decimal


@pytest.fixture(autouse=True)
def english():
    with translation.override("en"):
        yield


def balances(organization, **kwargs):
    return {b.account.code: b.net_debit for b in account_balances(organization, **kwargs)}


def test_straight_line_adds_up_to_the_amount():
    months = straight_line(D("1000000"), 3, (2026, 11))
    assert [(m.year, m.month, m.amount) for m in months] == [
        (2026, 11, D("333333.33")), (2026, 12, D("333333.33")), (2027, 1, D("333333.34")),
    ]
    assert months[-1].recognised == D("1000000") and months[-1].period_end == date(2027, 1, 31)


@pytest.fixture
def rent(organization, supplier, account):
    """A year's shop rent for 2026, billed and booked straight to Beban Sewa in December 2025."""
    invoicing.issue_invoice(invoice=invoicing.create_invoice(
        organization=organization, kind=InvoiceKind.PURCHASE, contact=supplier, date=date(2025, 12, 20),
        lines=[InvoiceLineInput("Sewa toko 2026", account("6-1100"), D("1"), D("12000000"))],
    ))
    return services.register_deferral(
        organization=organization, kind=DeferralKind.PREPAID_EXPENSE, description="Sewa toko 2026", amount=D("12000000"),
        recorded_on=date(2025, 12, 20), starts_on=date(2026, 1, 15), months=12,
        balance_account=account("1-1600"), recognition_account=account("6-1100"), reclassify=True,
    )


def test_rent_paid_in_advance_is_expensed_over_the_lease(organization, account, rent):
    assert rent.starts_on == date(2026, 1, 1) and rent.ends_on == date(2026, 12, 31)
    # 2025 carries none of it: it is an asset until the months it covers.
    assert income_statement(organization, date(2025, 1, 1), date(2025, 12, 31)).net_profit == 0
    assert balances(organization, date_to=date(2025, 12, 31))["1-1600"] == D("12000000")

    entries = services.post_deferrals(organization=organization, through=date(2026, 3, 31))
    assert [e.date for e in entries] == [date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31)]
    assert {e.source for e in entries} == {JournalEntry.Source.DEFERRAL}
    assert services.post_deferrals(organization=organization, through=date(2026, 3, 31)) == []  # each month once
    after = balances(organization, date_to=date(2026, 3, 31))
    assert (after["1-1600"], after["6-1100"]) == (D("9000000"), D("3000000"))
    assert verify_chain(organization) == []

    with pytest.raises(InvalidStateTransition, match="recognised"):  # it stays in the register
        services.void_deferral(deferral=rent, on=date(2026, 4, 1))


def test_rent_received_in_advance_is_revenue_over_the_contract(organization, account, auth_client):
    create_journal_entry(organization=organization, date=date(2026, 6, 30), description="Sewa gudang diterima di muka", lines=[
        JournalLineInput(account("1-1100"), debit=D("6000000")), JournalLineInput(account("2-1500"), credit=D("6000000")),
    ])
    deferral = services.register_deferral(
        organization=organization, kind=DeferralKind.DEFERRED_REVENUE, description="Sewa gudang Jul-Des 2026", amount=D("6000000"),
        recorded_on=date(2026, 6, 30), starts_on=date(2026, 7, 1), months=6,
        balance_account=account("2-1500"), recognition_account=account("4-2300"), reclassify=False,
    )
    assert deferral.opening_entry is None
    services.post_deferrals(organization=organization, through=date(2026, 9, 30))
    after = balances(organization, date_to=date(2026, 9, 30))
    assert (after["2-1500"], after["4-2300"]) == (D("-3000000"), D("-3000000"))

    page = auth_client.get(reverse("deferrals:detail", args=[deferral.pk]))
    assert page.status_code == 200 and page.context["recognised"] == D("3000000") and page.context["remaining"] == D("3000000")
    assert auth_client.get(reverse("deferrals:list")).status_code == 200


def test_a_mistake_is_voided_before_any_month_is_recognised(organization, account, rent):
    voided = services.void_deferral(deferral=rent, on=date(2026, 1, 5))
    assert voided.status == Deferral.Status.VOID
    assert balances(organization, date_to=date(2026, 1, 5))["1-1600"] == 0  # the reclassification is reversed
    assert services.post_deferrals(organization=organization, through=date(2026, 12, 31)) == []
    with pytest.raises(InvalidStateTransition):
        services.void_deferral(deferral=rent, on=date(2026, 1, 5))


def test_the_database_keeps_the_register(organization, account, rent):
    with pytest.raises(IntegrityError), transaction.atomic():
        Deferral.objects.filter(pk=rent.pk).update(amount=D("1"))
    with pytest.raises(IntegrityError), transaction.atomic():
        Deferral.objects.filter(pk=rent.pk).delete()
    with pytest.raises(IntegrityError), transaction.atomic():  # voided only through its reversal
        Deferral.objects.filter(pk=rent.pk).update(status=Deferral.Status.VOID)
    Deferral.objects.filter(pk=rent.pk).update(description="Sewa toko Jl. Raya 2026")

    [entry] = services.post_deferrals(organization=organization, through=date(2026, 1, 31))
    posting = DeferralPosting.objects.get(deferral=rent)
    with pytest.raises(IntegrityError), transaction.atomic():
        DeferralPosting.objects.filter(pk=posting.pk).update(amount=D("1"))
    with pytest.raises(IntegrityError), transaction.atomic():
        DeferralPosting.objects.filter(pk=posting.pk).delete()
    with pytest.raises(IntegrityError), transaction.atomic():  # beyond the term
        DeferralPosting.objects.create(organization=organization, deferral=rent, year=2027, month=1, amount=D("1"), journal_entry=entry)


@pytest.mark.parametrize("change, message", [
    ({"balance_account": "6-1100"}, "other current asset"),
    ({"recognition_account": "4-1000"}, "expense account"),
    ({"recorded_on": date(2026, 2, 1)}, "end of the first month"),
    ({"months": 0}, "term"),
])
def test_what_a_prepayment_needs(organization, account, change, message):
    data = dict(
        organization=organization, kind=DeferralKind.PREPAID_EXPENSE, description="Asuransi", amount=D("1200000"),
        recorded_on=date(2026, 1, 10), starts_on=date(2026, 1, 1), months=12,
        balance_account=account("1-1600"), recognition_account=account("6-1900"), reclassify=False,
    )
    data.update({key: account(value) if key.endswith("account") else value for key, value in change.items()})
    with pytest.raises(ValidationFailed, match=message):
        services.register_deferral(**data)


def test_registering_from_the_page(organization, account, auth_client, other_organization):
    url = reverse("deferrals:create", args=[DeferralKind.PREPAID_EXPENSE])
    assert auth_client.get(url).status_code == 200
    assert auth_client.get(reverse("deferrals:create", args=["unknown"])).status_code == 404
    response = auth_client.post(url, {
        "description": "Sewa ruko 2026", "amount": "24000000", "recorded_on": "2026-01-02", "starts_on": "2026-01-01", "months": "24",
        "balance_account": account("1-1600").pk, "recognition_account": account("6-1100").pk,
    })
    deferral = Deferral.objects.get(organization=organization)
    assert response.status_code == 302 and deferral.opening_entry is None and deferral.months == 24
    foreign = Deferral.objects.create(
        organization=other_organization, number="DM/1", kind=DeferralKind.PREPAID_EXPENSE, description="x", amount=D("1"),
        recorded_on=date(2026, 1, 1), starts_on=date(2026, 1, 1), months=1,
        balance_account=account("1-1600"), recognition_account=account("6-1100"),
    )
    assert auth_client.get(reverse("deferrals:detail", args=[foreign.pk])).status_code == 404


def test_the_nightly_command_catches_up(organization, account, rent, capsys):
    ended = sum(month.period_end <= timezone.localdate() for month in rent.schedule)
    call_command("post_deferrals")
    assert DeferralPosting.objects.filter(deferral=rent).count() == ended  # every month that has ended, once
    assert f"Posted {ended} entr" in capsys.readouterr().out


def test_an_investment_in_a_joint_venture_is_an_asset_at_cost(organization, account, auth_client):
    create_journal_entry(organization=organization, date=date(2026, 2, 1), description="Setoran ventura bersama", lines=[
        JournalLineInput(account("1-3000"), debit=D("50000000")), JournalLineInput(account("1-1100"), credit=D("50000000")),
    ])
    bs = balance_sheet(organization, date(2026, 12, 31))
    assert [(r.code, r.amount) for r in bs.investments.rows] == [("1-3000", D("50000000"))]
    assert bs.is_balanced
    report = auth_client.get(reverse("reports:notes"), {"as_of": "2026-12-31"}).context["report"]
    assert report.balance_sheet.investments.note in [n.section.note for n in report.item_notes]
