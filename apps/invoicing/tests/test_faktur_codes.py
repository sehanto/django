"""The rest of the Faktur Pajak: PPnBM, transaction codes 05/06/09/10, fixed assets sold on an
invoice, places of business, and income tax given back by refunds."""

from datetime import date
from decimal import Decimal
from xml.etree import ElementTree as ET

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import translation

from apps.assets import services as assets
from apps.contacts.models import Contact
from apps.core.exceptions import InvalidStateTransition, ValidationFailed
from apps.invoicing import coretax, services
from apps.invoicing.models import DeliveryKind, IncomeTaxWithheld, InvoiceKind
from apps.invoicing.selectors import vat_summary
from apps.invoicing.services import IncomeTaxInput, InvoiceLineInput, ReturnLineInput
from apps.ledger.models import Account
from apps.ledger.selectors import account_balance
from apps.taxes.models import TaxRate
from apps.taxes.services import record_pph_final_payment
from apps.tenants.models import BusinessPlace

D = Decimal


@pytest.fixture(autouse=True)
def english():
    with translation.override("en"):
        yield


@pytest.fixture
def books(pkp_organization, customer, supplier):
    pkp_organization.npwp = "0012345678901234"
    pkp_organization.save(update_fields=["npwp"])
    customer.npwp = "3174061502560010"
    customer.save()
    supplier.npwp = "1091031210920664"
    supplier.save()
    return pkp_organization


def balance(organization, code):
    return account_balance(organization, Account.objects.get(organization=organization, code=code))


def rate(organization, name, percent, numerator=1, denominator=1, **kwargs):
    tax = TaxRate(organization=organization, name=name, rate=D(percent), dpp_numerator=numerator, dpp_denominator=denominator, **kwargs)
    tax.full_clean()
    tax.save()
    return tax


def sell(books, contact, account, tax, price="10000000", **kwargs):
    return services.issue_invoice(invoice=services.create_invoice(
        organization=books, kind=InvoiceKind.SALES, contact=contact, date=date(2026, 3, 5),
        lines=[InvoiceLineInput("Barang", account("4-1000"), D("1"), D(price), tax_rate=tax)], **kwargs,
    ))


def faktur(books, invoice):
    return ET.fromstring(coretax.build_invoices_xml(books, [invoice])).find("ListOfTaxInvoice/TaxInvoice")


# --- PPnBM --------------------------------------------------------------------------------


def test_ppnbm_is_charged_posted_and_exported(books, customer, account):
    luxury = rate(books, "PPN 12% + PPnBM 20%", "12", luxury_rate=D("20"))
    invoice = sell(books, customer, account, luxury)
    assert (invoice.tax_amount, invoice.luxury_tax_amount, invoice.total) == (D("1200000"), D("2000000"), D("13200000"))
    assert balance(books, "2-1150") == D("2000000") and balance(books, "2-1100") == D("1200000")
    good = faktur(books, invoice).find("ListOfGoodService/GoodService")
    assert [good.findtext(t) for t in ("VATRate", "VAT", "STLGRate", "STLG")] == ["12", "1200000", "20", "2000000"]
    assert vat_summary(books, 2026)[2]["luxury"] == D("2000000")

    note = services.create_return(invoice=invoice, date=date(2026, 3, 9), reason="Cacat", lines=[ReturnLineInput(invoice.lines.get(), D("1"))])
    assert (note.luxury_tax_amount, note.total) == (D("2000000"), D("13200000"))
    assert balance(books, "2-1150") == 0 and vat_summary(books, 2026)[2]["luxury"] == 0


def test_ppnbm_on_purchases_is_cost_and_goes_on_the_return_file(books, supplier, account):
    luxury = rate(books, "PPN 12% + PPnBM 10%", "12", luxury_rate=D("10"))
    bill = services.issue_invoice(invoice=services.create_invoice(
        organization=books, kind=InvoiceKind.PURCHASE, contact=supplier, date=date(2026, 3, 1), tax_invoice_number="04002500000348920",
        lines=[InvoiceLineInput("Mobil", account("1-2200"), D("1"), D("100000000"), tax_rate=luxury)],
    ))
    assert balance(books, "1-2200") == D("110000000")  # PPnBM can't be credited: it is part of the cost
    assert balance(books, "1-1400") == D("12000000")
    note = services.create_return(
        invoice=bill, date=date(2026, 3, 10), reason="Salah kirim", lines=[ReturnLineInput(bill.lines.get(), D("1"))],
        cash_account=account("1-1100"),
    )
    root = ET.fromstring(coretax.build_xml(books, [note]))
    assert root.findtext("InputReturnDataList/InputReturnData/TransactionDocumentData/ReturnSTLG") == "10000000"
    row = root.find("InputReturnDataList/InputReturnData/TransactionDetailsData/Rows")
    assert (row.findtext("STLGRate"), row.findtext("ReturnSTLG")) == ("10", "10000000")
    assert balance(books, "1-2200") == 0


def test_a_collector_withholds_ppn_and_ppnbm(books, customer, account):
    customer.vat_collector = Contact.VatCollector.OTHER
    customer.save()
    luxury = rate(books, "PPN 12% + PPnBM 20%", "12", luxury_rate=D("20"))
    invoice = sell(books, customer, account, luxury)
    services.record_payment(invoice=invoice, date=date(2026, 3, 20), amount=D("13200000"), cash_account=account("1-1100"), vat_withheld=D("3200000"))
    assert (balance(books, "2-1100"), balance(books, "2-1150"), balance(books, "1-1100")) == (0, 0, D("10000000"))
    assert faktur(books, invoice).findtext("TrxCode") == "03"


# --- Transaction codes 05, 06, 09, 10 ------------------------------------------------------


def test_besaran_tertentu_uses_code_05(books, customer, account):
    with pytest.raises(ValidationError, match="DPP 1/1"):
        rate(books, "salah", "1.2", 11, 12, is_specific_amount=True)
    specific = rate(books, "PPN besaran tertentu 1,2%", "1.2", is_specific_amount=True)
    invoice = sell(books, customer, account, specific)
    assert invoice.tax_amount == D("120000")
    element = faktur(books, invoice)
    assert element.findtext("TrxCode") == "05"
    good = element.find("ListOfGoodService/GoodService")
    assert [good.findtext(t) for t in ("TaxBase", "OtherTaxBase", "VATRate", "VAT")] == ["10000000", "10000000", "12", "120000"]

    luxury = TaxRate.objects.get(organization=books, dpp_numerator=1, is_specific_amount=False)  # also the full price
    mixed = services.issue_invoice(invoice=services.create_invoice(
        organization=books, kind=InvoiceKind.SALES, contact=customer, date=date(2026, 3, 5),
        lines=[InvoiceLineInput("A", account("4-1000"), D("1"), D("1000"), tax_rate=specific),
               InvoiceLineInput("B", account("4-1000"), D("1"), D("1000"), tax_rate=luxury)],
    ))
    assert "besaran tertentu" in coretax.invoice_problem(mixed)
    customer.vat_collector = Contact.VatCollector.OTHER  # a collector's code comes first
    customer.save()
    assert faktur(books, sell(books, customer, account, specific)).findtext("TrxCode") == "03"


def test_another_rate_uses_code_10(books, customer, account):
    old = rate(books, "PPN 11%", "11")
    good = faktur(books, sell(books, customer, account, old))
    assert (good.findtext("TrxCode"), good.findtext("ListOfGoodService/GoodService/VATRate")) == ("10", "11")


def test_a_tourists_vat_refund_uses_code_06(books, customer, account):
    standard = TaxRate.objects.filter(organization=books, dpp_numerator=11).first()
    with pytest.raises(ValidationFailed, match="foreign passport"):
        sell(books, customer, account, standard, delivery_kind=DeliveryKind.TOURIST)
    tourist = Contact.objects.create(
        organization=books, kind=Contact.Kind.CUSTOMER, name="Jane Doe", id_type=Contact.BuyerIdType.PASSPORT,
        id_number="E1234567", country="AUS",
    )
    element = faktur(books, sell(books, tourist, account, standard, delivery_kind=DeliveryKind.TOURIST))
    assert [element.findtext(t) for t in ("TrxCode", "BuyerTin", "BuyerDocument", "BuyerCountry", "BuyerDocumentNumber")] == [
        "06", "0000000000000000", "Passport", "AUS", "E1234567",
    ]


@pytest.fixture
def machine(books, account):
    asset = assets.register_asset(
        organization=books, name="Mesin sangrai", description="", asset_account=account("1-2300"),
        acquisition_date=date(2025, 1, 1), cost=D("24000000"), is_depreciable=True, useful_life_months=24,
        residual_value=D("0"), accumulated_depreciation_account=account("1-2900"),
        depreciation_expense_account=account("6-1600"), depreciated_before_until=None,
    )
    assets.post_depreciation(organization=books, through=date(2026, 2, 28))
    return asset


def test_a_fixed_asset_sold_on_an_invoice_uses_code_09(books, customer, account, machine):
    standard = TaxRate.objects.filter(organization=books, dpp_numerator=11).first()
    asset = assets.dispose_asset(
        asset=machine, on=date(2026, 3, 5), proceeds=D("6000000"), gain_loss_account=account("4-2000"),
        sale=assets.InvoicedSale(customer, standard),
    )
    invoice = asset.disposal_invoice
    # 14 months of 1.000.000 depreciated: book value 10.000.000, sold for 6.000.000 + PPN 660.000.
    assert (invoice.delivery_kind, invoice.subtotal, invoice.tax_amount) == ("09", D("6000000"), D("660000"))
    # (The purchase wasn't booked in this test: the cost leaves the asset account in full.)
    assert (balance(books, "1-2300"), balance(books, "1-2900"), balance(books, "1-1200")) == (D("-24000000"), 0, D("6660000"))
    assert balance(books, "4-2000") == D("-4000000")  # the loss (other income, debit balance)
    assert faktur(books, invoice).findtext("TrxCode") == "09"
    assert not invoice.can_return
    with pytest.raises(InvalidStateTransition, match="fixed asset"):
        services.void_invoice(invoice=invoice, on=date(2026, 3, 6))


# --- Places of business -------------------------------------------------------------------


def test_places_of_business_are_managed_in_the_settings(auth_client, books):
    url = reverse("tenants:place_create")
    with translation.override("en"):
        response = auth_client.post(url, {"name": "Cabang", "nitku": "9999999999999999000001", "address": "", "city": "", "is_active": "on"})
        assert response.status_code == 200 and "nitku" in response.context["form"].errors
        response = auth_client.post(url, {"name": "Cabang Sleman", "nitku": "0012 3456 7890 1234 000001", "address": "", "city": "Sleman", "is_active": "on"})
    assert response.status_code == 302
    place = BusinessPlace.objects.get(organization=books)
    assert place.nitku == "0012345678901234000001"
    page = auth_client.get(reverse("tenants:settings"))
    assert list(page.context["places"]) == [place]
    form = auth_client.get(reverse("invoicing:create", args=[InvoiceKind.SALES])).context["form"]
    assert list(form.fields["business_place"].queryset) == [place]


# --- Income tax given back by refunds --------------------------------------------------------


def test_a_refund_gives_back_the_income_tax_withheld_on_it(books, customer, account):
    invoice = sell(books, customer, account, None)  # 10.000.000 for services, no PPN

    def pay(amount, kind, tax):
        return services.record_payment(
            invoice=invoice, date=date(2026, 3, 20), amount=D(amount), cash_account=account("1-1100"),
            income_tax=IncomeTaxInput(kind, D(tax), account("1-1500")),
        )

    first = pay("6000000", IncomeTaxWithheld.PPH_23, "120000")
    with pytest.raises(ValidationFailed, match="another income tax"):  # one kind per invoice
        pay("4000000", IncomeTaxWithheld.PPH_22, "60000")
    pay("4000000", IncomeTaxWithheld.PPH_23, "80000")
    note = services.create_return(
        invoice=invoice, date=date(2026, 3, 25), reason="Batal", lines=[ReturnLineInput(invoice.lines.get(), D("0.25"))],
        cash_account=account("1-1100"),
    )
    # A quarter refunded: 2.500.000, of which the buyer had withheld 2% = 50.000.
    assert (note.total, note.income_tax_withheld, note.refunded, note.income_tax_kind) == (D("2500000"), D("50000"), D("2450000"), "pph23")
    assert balance(books, "1-1500") == D("150000") and balance(books, "1-1100") == D("9800000") - D("2450000")
    services.void_payment(payment=first, on=date(2026, 3, 26))  # 150.000 still held covers what the refund gave back
    second = invoice.payments.get(status="posted")
    with pytest.raises(InvalidStateTransition, match="Refunds gave back income tax"):
        services.void_payment(payment=second, on=date(2026, 3, 26))
    services.void_return(note=note, on=date(2026, 3, 26))
    services.void_payment(payment=second, on=date(2026, 3, 26))


def test_pph_final_given_back_counts_in_the_month_of_the_refund(other_organization):
    from apps.taxes.selectors import pph_final_withheld

    cv = other_organization  # a CV: every sale carries PPh Final

    def acc(code):
        return Account.objects.get(organization=cv, code=code)

    buyer = Contact.objects.create(organization=cv, kind=Contact.Kind.CUSTOMER, name="Dinas")
    invoice = services.issue_invoice(invoice=services.create_invoice(
        organization=cv, kind=InvoiceKind.SALES, contact=buyer, date=date(2026, 3, 5),
        lines=[InvoiceLineInput("Jasa", acc("4-1100"), D("1"), D("20000000"))],
    ))
    services.record_payment(
        invoice=invoice, date=date(2026, 3, 20), amount=D("20000000"), cash_account=acc("1-1100"),
        income_tax=IncomeTaxInput(IncomeTaxWithheld.PPH_FINAL, D("100000")),
    )
    with pytest.raises(ValidationFailed, match="nothing to deposit"):  # the buyer withheld all of March's
        record_pph_final_payment(organization=cv, year=2026, month=3, paid_on=date(2026, 4, 10), cash_account=acc("1-1100"))
    note = services.create_return(
        invoice=invoice, date=date(2026, 4, 3), reason="Batal", lines=[ReturnLineInput(invoice.lines.get(), D("0.5"))],
        cash_account=acc("1-1100"),
    )
    assert (note.income_tax_kind, note.income_tax_withheld, note.refunded) == ("pph_final", D("50000"), D("9950000"))
    assert pph_final_withheld(cv, 2026) == {3: D("100000"), 4: D("-50000")}
    assert account_balance(cv, acc("2-1200")) == D("-50000")  # accrued at month end, then settled by what is withheld


def test_the_database_guards_asset_invoices_and_refunded_income_tax(books, customer, account, machine):
    from django.db import IntegrityError, transaction

    from apps.assets.models import FixedAsset
    from apps.invoicing.models import ReturnNote
    from apps.ledger.services import JournalLineInput, create_journal_entry

    invoice = sell(books, customer, account, None)
    with pytest.raises(IntegrityError), transaction.atomic():  # only with the disposal
        FixedAsset.objects.filter(pk=machine.pk).update(disposal_invoice=invoice)
    entry = create_journal_entry(
        organization=books, date=date(2026, 3, 25), description="x",
        lines=[JournalLineInput(account("4-1000"), debit=D("11")), JournalLineInput(account("1-1200"), credit=D("11"))],
    )
    with pytest.raises(IntegrityError), transaction.atomic():  # the buyer withheld no income tax on this invoice
        ReturnNote.objects.create(
            organization=books, kind=InvoiceKind.SALES, number="RJ-X", invoice=invoice, date=date(2026, 3, 25), reason="x",
            subtotal=D("11"), tax_amount=0, total=D("11"), credited=D("10"), refunded=0, income_tax_withheld=D("1"),
            income_tax_kind=IncomeTaxWithheld.PPH_23, journal_entry=entry,
        )
