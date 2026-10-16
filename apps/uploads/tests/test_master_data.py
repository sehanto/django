"""Contacts, items and accounts from a spreadsheet."""

from decimal import Decimal

import pytest

from apps.contacts.models import Contact
from apps.invoicing.models import Item
from apps.ledger.models import Account
from apps.uploads.domain import RowOutcome
from apps.uploads.models import Upload

from .conftest import messages


def test_contacts_are_added_with_their_region_from_the_code_or_the_postcode(organization, upload, jatake):
    report = upload("contacts", (
        "Nama;Jenis;NPWP;Alamat;Kode desa;Kode pos;Termin\n"
        "PT Baru Jaya;pelanggan;01.234.567.8-901.000;Jl. Raya 1;3603222008;15330;45\n"
        "CV Pemasok;supplier;;Jl. Pasar 2;;15330;\n"
    ))
    assert report.ok, messages(report)
    first, second = Contact.objects.filter(organization=organization).order_by("name")
    assert (second.name, second.kind, second.npwp, second.village, second.payment_terms_days) == (
        "PT Baru Jaya", Contact.Kind.CUSTOMER, "012345678901000", jatake, 45)
    assert (first.kind, first.village) == (Contact.Kind.SUPPLIER, jatake)  # the only kelurahan/desa with 15330 here
    assert Upload.objects.get(organization=organization).applied == 2


def test_rows_are_checked_like_the_page_and_nothing_is_kept_on_an_error(organization, upload, jatake):
    report = upload("contacts", (
        "name,npwp,village code,postcode,email\n"
        "PT Satu,,36.03.22.2008,15331,\n"  # wrong postcode for Jatake
        "PT Dua,123,,,\n"  # not an NPWP
        "PT Tiga,,,,bukan-email\n"
        "PT Empat,,,,\n"
    ))
    assert not report.ok
    errors = {number: message for number, outcome, message in messages(report) if outcome == RowOutcome.ERROR}
    assert set(errors) == {2, 3, 4}
    assert "15330" in errors[2]
    assert not Contact.objects.filter(organization=organization).exists() and not Upload.objects.exists()


def test_existing_contacts_are_skipped_never_overwritten(organization, upload, customer):
    report = upload("contacts", "name,email\npt pelanggan,new@example.com\nPT Lain,\n")
    assert [outcome for _n, outcome, _m in messages(report)] == [RowOutcome.SKIPPED, RowOutcome.CREATED]
    customer.refresh_from_db()
    assert customer.email == ""


def test_items_take_accounts_by_code_and_the_ppn_rate_by_name(organization, upload, account):
    report = upload("items", (
        "Nama Barang,Kode Barang,Jenis,Satuan,Harga Jual,Harga Beli,Akun Pendapatan,Tarif PPN\n"
        'Kopi,KOPI-1,barang,pcs,"45.000",30000,4-1000,\n'
        "Instalasi,JASA-1,jasa,jam,150000,,4-1100,\n"
    ))
    assert report.ok, messages(report)
    kopi = Item.objects.get(organization=organization, code="KOPI-1")
    assert (kopi.sale_price, kopi.purchase_price, kopi.income_account) == (Decimal("45000"), Decimal("30000"), account("4-1000"))
    assert Item.objects.get(code="JASA-1").type == Item.Type.SERVICE
    again = upload("items", "name,code,revenue account\nKopi lagi,KOPI-1,4-1000\nX,,9-9999\n", dry=True)
    assert messages(again)[0][1] == RowOutcome.SKIPPED
    assert "9-9999" in messages(again)[1][2]


def test_tracked_items_need_their_accounts(organization, upload):
    report = upload("items", "name,track stock,inventory account,cost of goods sold account\nBeras,ya,1-1300,5-1000\nGula,ya,,\n", dry=True)
    assert [o for _n, o, _m in messages(report)] == [RowOutcome.CREATED, RowOutcome.ERROR]


def test_accounts_are_added_by_category_in_either_language(organization, upload, account):
    report = upload("accounts", (
        "Kode Akun,Nama Akun,Kategori,Akun kontra\n"
        "1-1110,Bank BCA 123,Kas dan setara kas,\n"
        "1-1100,Bank lagi,cash_bank,\n"  # the code is taken
        "4-1300,Diskon Penjualan,Operating revenue,ya\n"
    ))
    assert report.ok, messages(report)
    assert [o for _n, o, _m in messages(report)] == [RowOutcome.CREATED, RowOutcome.SKIPPED, RowOutcome.CREATED]
    assert account("1-1110").category == "cash_bank" and account("4-1300").is_contra
    assert account("1-1100").name == "Bank"


@pytest.mark.parametrize("kind", ["contacts", "items", "accounts"])
def test_the_same_file_is_never_applied_twice(organization, upload, kind):
    content = {"contacts": "name\nPT X\n", "items": "name\nBarang X\n", "accounts": "code,name,category\n6-1999,Beban X,operating_expense\n"}[kind]
    assert upload(kind, content).ok
    with pytest.raises(Exception, match="already applied"):
        upload(kind, content)
    assert Upload.objects.filter(organization=organization, kind=kind).count() == 1


def test_an_upload_stays_in_its_organization(organization, other_organization, upload):
    assert upload("contacts", "name\nPT X\n", org=other_organization).ok
    assert not Contact.objects.filter(organization=organization).exists()
    assert Account.objects.filter(organization=other_organization).exists()
