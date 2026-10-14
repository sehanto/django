"""SAK EMKM rules the books follow beyond the basic statements: prior-period errors corrected
retrospectively (7.16), receivables written off without an allowance (8.10-8.13), depreciation
and amortisation without a residual value (11.14, 12.13), intangible assets (Bab 12), policies
applied consistently (7.5), the accounts the standard names, and statement items that refer to
their notes (6.4)."""

from datetime import date
from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import translation

from apps.assets import services as assets
from apps.core.exceptions import InvalidStateTransition, ValidationFailed
from apps.inventory import services as inventory
from apps.invoicing import services as invoicing
from apps.invoicing.models import Invoice, InvoiceKind, Item, ReceivableWriteOff
from apps.invoicing.services import InvoiceLineInput
from apps.ledger.domain import AccountCategory
from apps.ledger.models import Account, JournalEntry
from apps.ledger.selectors import account_balances, verify_chain
from apps.ledger.services import JournalLineInput, create_journal_entry, reverse_journal_entry
from apps.reports.statements import build_trial_balance
from apps.reports.views import balance_sheet, income_statement
from apps.tenants.forms import OrganizationSettingsForm
from apps.tenants.models import Organization
from apps.tenants.services import create_organization

D = Decimal


@pytest.fixture(autouse=True)
def english():
    with translation.override("en"):
        yield


def journal(organization, on, description, *lines, **kwargs):
    return create_journal_entry(organization=organization, date=on, description=description, lines=list(lines), **kwargs)


def test_a_prior_period_error_is_corrected_retrospectively(organization, account, auth_client):
    journal(organization, date(2025, 3, 1), "Penjualan tunai",
            JournalLineInput(account("1-1000"), debit=D("10000000")), JournalLineInput(account("4-1000"), credit=D("10000000")))
    journal(organization, date(2026, 2, 1), "Penjualan tunai",
            JournalLineInput(account("1-1000"), debit=D("4000000")), JournalLineInput(account("4-1000"), credit=D("4000000")))
    # Found in 2026: December 2025's rent of Rp1.000.000 was never recorded.
    correction = journal(organization, date(2026, 3, 10), "Koreksi: sewa Desember 2025 belum dicatat", restates_year=2025,
                         *[JournalLineInput(account("6-1100"), debit=D("1000000")), JournalLineInput(account("2-1400"), credit=D("1000000"))])

    pl = income_statement(organization, date(2026, 1, 1), date(2026, 12, 31))
    assert (pl.net_profit, pl.previous_net_profit) == (D("4000000"), D("9000000"))  # 2025 restated, 2026 untouched
    assert pl.comparative_restated
    bs = balance_sheet(organization, date(2026, 12, 31))
    assert bs.is_balanced and bs.comparative_restated
    assert (bs.prior_years_earnings, bs.current_year_earnings) == (D("9000000"), D("4000000"))  # through opening retained earnings
    assert bs.current_liabilities.previous_total == D("1000000")  # the comparative position shows the liability
    # The books keep the entry on its own date.
    assert build_trial_balance(account_balances(organization, date_to=date(2025, 12, 31)), date(2025, 12, 31)).total_debit == D("10000000")

    notes = auth_client.get(reverse("reports:notes"), {"as_of": "2026-12-31"}).context["report"]
    ((entry, effect),) = [(c.entry, c.profit_effect) for c in notes.corrections]
    assert (entry, effect) == (correction, D("-1000000"))
    assert notes.corrections_note == notes.additional_note - 1

    with pytest.raises(IntegrityError), transaction.atomic():  # fixed once posted, like the rest of the entry
        JournalEntry.objects.filter(pk=correction.pk).update(restates_year=2024)
    reversal = reverse_journal_entry(entry=correction, date=date(2026, 3, 11))
    assert reversal.restates_year == 2025  # undoing it restates the same year back
    assert income_statement(organization, date(2026, 1, 1), date(2026, 12, 31)).previous_net_profit == D("10000000")
    assert verify_chain(organization) == []


@pytest.mark.parametrize("year, on", [(2026, date(2026, 3, 1)), (2027, date(2026, 3, 1)), (1999, date(2026, 3, 1))])
def test_a_correction_restates_an_earlier_year(organization, account, year, on):
    with pytest.raises(ValidationFailed, match="earlier year"):
        journal(organization, on, "Koreksi", JournalLineInput(account("6-1100"), debit=D("1")),
                JournalLineInput(account("2-1400"), credit=D("1")), restates_year=year)


def sale(organization, customer, account, amount=D("1000000")):
    return invoicing.issue_invoice(invoice=invoicing.create_invoice(
        organization=organization, kind=InvoiceKind.SALES, contact=customer, date=date(2026, 3, 1),
        lines=[InvoiceLineInput("Jasa", account("4-1100"), D("1"), amount)],
    ))


def test_an_uncollectible_receivable_is_written_off(organization, customer, account):
    invoice = sale(organization, customer, account)
    invoicing.record_payment(invoice=invoice, date=date(2026, 3, 5), amount=D("400000"), cash_account=account("1-1100"))
    with pytest.raises(ValidationFailed, match="reason"):
        invoicing.write_off_receivable(invoice=invoice, date=date(2026, 6, 30), reason="  ")
    write_off = invoicing.write_off_receivable(invoice=invoice, date=date(2026, 6, 30), reason="Pelanggan pailit")
    invoice.refresh_from_db()
    assert (write_off.amount, invoice.status, invoice.balance_due) == (D("600000"), Invoice.Status.WRITTEN_OFF, D("0"))
    balances = {b.account.code: b.net_debit for b in account_balances(organization)}
    assert (balances["6-1800"], balances["1-1200"]) == (D("600000"), D("0"))  # a loss, no allowance
    with pytest.raises(InvalidStateTransition):
        invoicing.record_payment(invoice=invoice, date=date(2026, 7, 1), amount=D("1"), cash_account=account("1-1100"))

    with pytest.raises(IntegrityError), transaction.atomic():  # the invoice must show its write-offs
        Invoice.objects.filter(pk=invoice.pk).update(amount_written_off=D("1"))
    with pytest.raises(IntegrityError), transaction.atomic():
        ReceivableWriteOff.objects.filter(pk=write_off.pk).update(amount=D("1"))

    invoicing.void_write_off(write_off=write_off, on=date(2026, 7, 1))
    invoice.refresh_from_db()
    assert (invoice.status, invoice.balance_due) == (Invoice.Status.PARTIAL, D("600000"))
    assert verify_chain(organization) == []


def test_a_written_off_sale_still_owes_its_ppn(pkp_organization, customer, account):
    """A write-off settles the receivable, not the delivery: the PPN stays due and the Faktur stays reported."""
    from apps.invoicing import coretax
    from apps.invoicing.selectors import vat_summary
    from apps.taxes.models import TaxRate

    pkp_organization.npwp = "0123456789012345"
    pkp_organization.save(update_fields=["npwp"])
    customer.npwp = "1091031210920664"
    customer.save(update_fields=["npwp"])
    ppn = TaxRate.objects.filter(organization=pkp_organization, dpp_numerator=11).first()
    invoice = invoicing.issue_invoice(invoice=invoicing.create_invoice(
        organization=pkp_organization, kind=InvoiceKind.SALES, contact=customer, date=date(2026, 3, 1),
        lines=[InvoiceLineInput("Jasa", account("4-1100"), D("1"), D("1000000"), tax_rate=ppn)],
    ))
    output = vat_summary(pkp_organization, 2026)[2]["output"]
    invoicing.write_off_receivable(invoice=invoice, date=date(2026, 6, 30), reason="Pelanggan pailit")
    invoice.refresh_from_db()
    assert invoice.status == Invoice.Status.WRITTEN_OFF and output > 0
    assert vat_summary(pkp_organization, 2026)[2]["output"] == output
    assert list(coretax.month_invoices(pkp_organization, 2026, 3)) == [invoice]
    assert coretax.invoice_problem(invoice) is None
    invoicing.record_tax_invoice_number(invoice=invoice, number="04002600000000001")


def test_only_open_sales_invoices_are_written_off(organization, supplier, customer, account):
    bill = invoicing.issue_invoice(invoice=invoicing.create_invoice(
        organization=organization, kind=InvoiceKind.PURCHASE, contact=supplier, date=date(2026, 3, 1),
        lines=[InvoiceLineInput("Sewa", account("6-1100"), D("1"), D("500000"))],
    ))
    with pytest.raises(InvalidStateTransition):
        invoicing.write_off_receivable(invoice=bill, date=date(2026, 6, 30), reason="x")
    paid = sale(organization, customer, account)
    invoicing.record_payment(invoice=paid, date=date(2026, 3, 5), amount=D("1000000"), cash_account=account("1-1100"))
    with pytest.raises(InvalidStateTransition):
        invoicing.write_off_receivable(invoice=paid, date=date(2026, 6, 30), reason="x")


def register(organization, account, **fields):
    data = dict(name="Laptop", asset_account=account("1-2300"), acquisition_date=date(2026, 1, 10), cost=D("12000000"),
                is_depreciable=True, useful_life_months=48, residual_value=D("0"),
                accumulated_depreciation_account=account("1-2900"), depreciation_expense_account=account("6-1600"))
    return assets.register_asset(organization=organization, **{**data, **fields})


def test_no_residual_value_and_intangibles_are_amortised(organization, account):
    with pytest.raises(ValidationFailed, match="without a residual value"):
        register(organization, account, residual_value=D("1000000"))
    with pytest.raises(ValidationFailed, match="amortised"):
        register(organization, account, name="Lisensi", asset_account=account("1-2500"), is_depreciable=False)
    with pytest.raises(ValidationFailed, match="same kind"):  # amortisation goes with intangibles
        register(organization, account, name="Lisensi", asset_account=account("1-2500"))
    journal(organization, date(2026, 1, 10), "Pembelian lisensi software",
            JournalLineInput(account("1-2500"), debit=D("2400000")), JournalLineInput(account("1-1100"), credit=D("2400000")))
    licence = register(organization, account, name="Lisensi software", asset_account=account("1-2500"), cost=D("2400000"),
                       useful_life_months=24, accumulated_depreciation_account=account("1-2950"), depreciation_expense_account=account("6-1650"))
    (entry,) = assets.post_depreciation(organization=organization, through=date(2026, 1, 31))
    assert entry.description == "Amortisasi aset takberwujud masa 01/2026"
    assert sum(line.debit for line in entry.lines.all()) == D("100000")  # 2.400.000 / 24, nothing left at the end
    bs = balance_sheet(organization, date(2026, 1, 31))
    assert [(r.code, r.amount) for r in bs.intangible_assets.rows] == [("1-2500", D("2400000")), ("1-2950", D("-100000"))]
    assert bs.is_balanced
    assert licence.schedule[-1].amount and sum(m.amount for m in licence.schedule) == D("2400000")


def test_policies_are_fixed_once_applied(organization, user, account):
    form = OrganizationSettingsForm(instance=organization, organization=organization, acting_user=user)
    assert not form.fields["inventory_cost_formula"].disabled and not form.fields["depreciation_method"].disabled
    item = Item.objects.create(organization=organization, code="KOPI", name="Kopi", unit="kg", income_account=account("4-1000"),
                               expense_account=account("5-1000"), track_inventory=True, inventory_account=account("1-1300"),
                               cogs_account=account("5-1000"))
    inventory.adjust_stock(organization=organization, item=item, on=date(2026, 1, 1), quantity=D("10"), unit_cost=D("1000"),
                           counter_account=account("3-1000"), reason="Stok awal")
    register(organization, account)
    form = OrganizationSettingsForm(instance=organization, organization=organization, acting_user=user)
    assert form.fields["inventory_cost_formula"].disabled and form.fields["depreciation_method"].disabled
    assert "7.5" in str(form.fields["inventory_cost_formula"].help_text)


def test_the_chart_has_what_sak_emkm_names(user, db):
    pt = create_organization(owner=user, name="PT Maju", legal_form=Organization.LegalForm.PT)
    sole = create_organization(owner=user, name="UD Maju", legal_form=Organization.LegalForm.PERORANGAN)
    names = {a.name: a.category for a in Account.objects.filter(organization=pt)}
    assert {
        "Aset Takberwujud": AccountCategory.INTANGIBLE_ASSET, "Pendapatan Diterima di Muka": AccountCategory.CURRENT_LIABILITY,
        "Utang Bank Jangka Pendek": AccountCategory.CURRENT_LIABILITY, "Beban Bunga": AccountCategory.FINANCE_COST,
        "Beban Pajak Penghasilan": AccountCategory.INCOME_TAX, "Pendapatan Hibah": AccountCategory.OTHER_INCOME,
        "Beban Piutang Tak Tertagih": AccountCategory.OPERATING_EXPENSE, "Tambahan Modal Disetor": AccountCategory.EQUITY,
    }.items() <= names.items()
    assert not Account.objects.filter(organization=sole, name="Tambahan Modal Disetor").exists()  # only for a PT (13.8)


def test_statement_items_refer_to_their_notes(auth_client, organization, account):
    journal(organization, date(2026, 2, 1), "Bunga pinjaman",
            JournalLineInput(account("7-1100"), debit=D("50000")), JournalLineInput(account("1-1100"), credit=D("50000")))
    pl = auth_client.get(reverse("reports:income_statement"), {"date_from": "2026-01-01", "date_to": "2026-12-31"}).context["report"]
    assert [r.code for r in pl.finance_costs.rows] == ["7-1100"] and pl.profit_before_tax == D("-50000")  # beban keuangan (5.2b)
    page = auth_client.get(reverse("reports:balance_sheet"), {"as_of": "2026-12-31"})
    numbers = [s.note for s in (page.context["report"].current_assets, page.context["report"].equity) if s.rows]
    assert numbers == [3] and f'#note-{numbers[0]}"'.encode() in page.content
    notes = auth_client.get(reverse("reports:notes"), {"as_of": "2026-12-31"})
    assert b'id="note-3"' in notes.content and b"(8.10)" in notes.content  # the receivables policy, in the user's language
