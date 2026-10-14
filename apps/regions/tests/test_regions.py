"""Indonesian addresses: the Kemendagri region list, loading it, and the cascading selects that
fill an address down to its kelurahan/desa (which fixes the postcode)."""

import gzip
import json
from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import translation

from apps.api.services import create_key
from apps.contacts.forms import ContactForm
from apps.contacts.models import Contact
from apps.invoicing import services as invoicing
from apps.invoicing.models import InvoiceKind
from apps.invoicing.services import InvoiceLineInput
from apps.payroll.forms import EmployeeForm
from apps.regions.models import Village
from apps.regions.services import load_regions, read_rows

D = Decimal
JATAKE = "36.03.22.2008"
CATURTUNGGAL = "34.04.07.2001"


@pytest.fixture(autouse=True)
def english():
    with translation.override("en"):
        yield


def test_the_list_is_complete_and_consistent():
    rows = read_rows()
    by_level = [sum(1 for code, *_ in rows if code.count(".") == depth) for depth in range(4)]
    assert by_level == [38, 514, 7285, 83762]  # Kepmendagri 300.2.2-2138/2025
    names = {code: (name, postal) for code, name, postal in rows}
    assert [names[code] for code in ("36", "36.03", "36.03.22", JATAKE)] == [
        ("Banten", ""), ("Kabupaten Tangerang", ""), ("Pagedangan", ""), ("Jatake", "15330"),
    ]


def test_a_bad_file_is_refused(tmp_path):
    path = tmp_path / "bad.csv.gz"
    with gzip.open(path, "wt", encoding="utf-8") as f:
        f.write("code,name,postal_code\n36,Banten,\n36.03.22.2008,Jatake,15330\n")  # no regency or district before it
    with pytest.raises(ValueError, match="before its parent"):
        read_rows(path)


def test_loading_adds_renames_and_retires_without_deleting(db, organization):
    rows = [row for row in read_rows() if row[0] in ("36", "36.03", "36.03.22", JATAKE, "36.03.22.2003")]
    result = load_regions(rows)
    assert (result.created, result.updated, result.deactivated) == (5, 0, 0)
    assert load_regions(rows) == type(result)(0, 0, 0)  # idempotent

    contact = Contact.objects.create(organization=organization, name="PT Tetap", village_id="36.03.22.2003")
    renamed = [(code, "Jatake Baru" if code == JATAKE else name, postal) for code, name, postal in rows if code != "36.03.22.2003"]
    result = load_regions(renamed)
    assert (result.created, result.updated, result.deactivated) == (0, 1, 1)
    assert Village.objects.get(code=JATAKE).name == "Jatake Baru"
    retired = Village.objects.get(code="36.03.22.2003")
    assert not retired.is_active  # a retired code stays, so the addresses using it do too
    contact.refresh_from_db()
    assert contact.village == retired


def test_the_selects_cascade_and_only_the_village_is_stored(organization, regions):
    villages = regions(JATAKE, CATURTUNGGAL)
    form = ContactForm(organization=organization)
    assert list(form.fields)[list(form.fields).index("address"):][:6] == [
        "address", "province", "regency", "district", "village", "postal_code",
    ]
    assert {p.code for p in form.fields["province"].queryset} == {"34", "36"}
    assert not form.fields["regency"].queryset.exists()  # filled once a province is chosen

    data = {"kind": "customer", "name": "PT Pelanggan", "payment_terms_days": "30", "document_language": "id",
            "address": "Jl. Raya Jatake 1", "province": "36", "regency": "36.03", "district": "36.03.22", "village": JATAKE}
    form = ContactForm(data=data, organization=organization)
    assert form.is_valid(), form.errors
    assert set(form.cleaned_data) & {"province", "regency", "district", "postal_code"} == set()
    contact = form.save(commit=False)
    assert contact.village == villages[JATAKE]
    assert contact.full_address == "Jl. Raya Jatake 1, Jatake, Pagedangan, Kabupaten Tangerang, Banten 15330"
    assert (contact.postal_code, contact.regency_name) == ("15330", "Kabupaten Tangerang")

    # The levels must agree with the village.
    form = ContactForm(data={**data, "province": "34"}, organization=organization)
    assert not form.is_valid() and "regency" in form.errors  # not a regency of DI Yogyakarta
    form = ContactForm(data={**data, "village": CATURTUNGGAL, "district": "", "regency": "", "province": "36"}, organization=organization)
    assert not form.is_valid() and form.errors["province"] == ["This doesn't match the kelurahan/desa chosen."]
    # Only the village (as the API sends it): the levels above follow.
    form = ContactForm(data={**data, "province": "", "regency": "", "district": ""}, organization=organization)
    assert form.is_valid(), form.errors
    # Malformed codes never reach the database.
    form = ContactForm(data={**data, "province": "36\x00", "village": "x"}, organization=organization)
    assert not form.is_valid()


def test_an_address_needs_its_village_except_abroad(organization, regions):
    regions(JATAKE)
    base = {"kind": "customer", "name": "PT Pelanggan", "payment_terms_days": "30", "document_language": "id"}
    assert ContactForm(data=base, organization=organization).is_valid()  # no address at all
    form = ContactForm(data={**base, "address": "Jl. Raya 1"}, organization=organization)
    assert not form.is_valid() and "village" in form.errors
    form = ContactForm(data={**base, "address": "Jl. Raya 1", "province": "36"}, organization=organization)
    assert not form.is_valid() and "village" in form.errors  # down to the kelurahan/desa
    abroad = {**base, "country": "SGP", "address": "1 Raffles Place, Singapore 048616"}
    assert ContactForm(data=abroad, organization=organization).is_valid()
    form = ContactForm(data={**abroad, "village": JATAKE}, organization=organization)
    assert not form.is_valid() and "village" in form.errors

    employee = {"name": "John Smith", "kind": "permanent", "pay_basis": "monthly", "nationality": "GBR", "passport_number": "P1",
                "ptkp_status": "TK/0", "start_date": "2026-01-01", "base_salary": "1", "fixed_allowance": "0", "zakat": "0",
                "gender": "", "address": "10 Downing Street, London"}
    assert EmployeeForm(data=employee, organization=organization).is_valid()  # a non-resident may live abroad
    form = EmployeeForm(data={**employee, "is_resident": "on"}, organization=organization)
    assert not form.is_valid() and "village" in form.errors


def test_the_choices_endpoint(auth_client, client, regions):
    regions(JATAKE, "36.03.22.2003")
    url = reverse("regions:choices")
    response = auth_client.get(url, {"level": "village", "parent": "36.03.22"})
    assert response.status_code == 200 and "max-age" in response["Cache-Control"]
    assert response.json()["choices"] == [
        {"code": JATAKE, "name": "Jatake", "postal_code": "15330"},
        {"code": "36.03.22.2003", "name": "Pagedangan", "postal_code": "15339"},
    ]
    assert auth_client.get(url, {"level": "regency", "parent": "36"}).json()["choices"] == [{"code": "36.03", "name": "Kabupaten Tangerang"}]
    for bad in ({"level": "village"}, {"level": "planet", "parent": "36"}, {"level": "village", "parent": "36.x"}):
        assert auth_client.get(url, bad).status_code == 404
    client.logout()
    assert client.get(url, {"level": "regency", "parent": "36"}).status_code == 302  # signed-in users only


def test_forms_render_the_selects(auth_client, regions):
    regions(JATAKE)
    page = auth_client.get(reverse("contacts:create"))
    assert b'data-region-level="province"' in page.content and b"js/regions.js" in page.content


def test_documents_carry_the_region_as_issued(organization, customer, account, regions):
    villages = regions(JATAKE, CATURTUNGGAL)
    organization.address, organization.village = "Jl. BSD Raya 1", villages[JATAKE]
    organization.save()
    customer.address, customer.village = "Jl. Kaliurang Km 5", villages[CATURTUNGGAL]
    customer.save()
    invoice = invoicing.issue_invoice(invoice=invoicing.create_invoice(
        organization=organization, kind=InvoiceKind.SALES, contact=customer, date=date(2026, 3, 5),
        lines=[InvoiceLineInput("Kopi", account("4-1000"), D("1"), D("100000"))],
    ))
    customer.village = villages[JATAKE]  # moves later
    customer.save()
    invoice.refresh_from_db()
    assert invoice.party_city == "Caturtunggal, Depok, Kabupaten Sleman, Daerah Istimewa Yogyakarta 55281"
    assert invoice.seller["city"] == "Jatake, Pagedangan, Kabupaten Tangerang, Banten 15330"


def test_the_api_takes_and_returns_the_region(client, organization, user, regions):
    regions(JATAKE)
    key = create_key(organization=organization, user=user, name="Toko online")[1]
    response = client.post(
        reverse("api:contacts"), json.dumps({"name": "PT Pembeli", "address": "Jl. Raya 1", "village": JATAKE}),
        content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {key}",
    )
    assert response.status_code == 201, response.content
    body = response.json()
    assert (body["village"], body["postal_code"], body["city"]) == (JATAKE, "15330", "Kabupaten Tangerang")
    assert body["region"] == "Jatake, Pagedangan, Kabupaten Tangerang, Banten 15330"


def test_the_postcode_finds_the_village_and_is_checked(organization, regions):
    regions(JATAKE, "36.03.22.2011", "36.03.22.2003")  # Jatake and Malang Nengah share 15330; Pagedangan has 15339
    base = {"kind": "customer", "name": "PT Pelanggan", "payment_terms_days": "30", "document_language": "id", "address": "Jl. Raya 1"}
    form = ContactForm(data={**base, "postal_code": "15339"}, organization=organization)
    assert form.is_valid(), form.errors
    assert form.cleaned_data["village"].code == "36.03.22.2003"  # one kelurahan/desa has it
    form = ContactForm(data={**base, "postal_code": "15330"}, organization=organization)
    assert not form.is_valid() and "Several" in form.errors["village"][0]
    form = ContactForm(data={**base, "postal_code": "15339", "village": JATAKE}, organization=organization)
    assert form.errors["postal_code"] == ["The postcode of Jatake is 15330."]
    for wrong in ("99999", "1533", "abcde"):
        assert "postal_code" in ContactForm(data={**base, "postal_code": wrong}, organization=organization).errors
    abroad = ContactForm(data={**base, "country": "SGP", "address": "1 Raffles Place", "postal_code": "04861"}, organization=organization)
    assert abroad.is_valid(), abroad.errors  # not looked up for an address abroad


def test_the_postcode_endpoint(auth_client, regions):
    regions(JATAKE, "36.03.22.2011")
    url = reverse("regions:choices")
    villages = auth_client.get(url, {"postcode": "15330"}).json()["villages"]
    assert [(v["code"], v["district"], v["regency"], v["province"]) for v in villages] == [
        (JATAKE, "36.03.22", "36.03", "36"), ("36.03.22.2011", "36.03.22", "36.03", "36"),
    ]
    assert villages[0]["label"] == "Jatake, Pagedangan, Kabupaten Tangerang, Banten 15330"
    assert auth_client.get(url, {"postcode": "1533x"}).status_code == 404


def test_issued_documents_keep_the_region_code(organization, customer, account, regions):
    villages = regions(JATAKE, CATURTUNGGAL)
    organization.village = villages[JATAKE]
    organization.save()
    customer.village = villages[CATURTUNGGAL]
    customer.save()
    invoice = invoicing.issue_invoice(invoice=invoicing.create_invoice(
        organization=organization, kind=InvoiceKind.SALES, contact=customer, date=date(2026, 3, 5),
        lines=[InvoiceLineInput("Kopi", account("4-1000"), D("1"), D("100000"))],
    ))
    assert (invoice.contact_village_id, invoice.seller_village_id) == (CATURTUNGGAL, JATAKE)


def test_contacts_are_filtered_by_province(auth_client, client, organization, user, regions):
    villages = regions(JATAKE, CATURTUNGGAL)
    Contact.objects.create(organization=organization, name="PT Banten", village=villages[JATAKE])
    Contact.objects.create(organization=organization, name="CV Jogja", village=villages[CATURTUNGGAL])
    page = auth_client.get(reverse("contacts:list"), {"province": "36"})
    assert [c.name for c in page.context["object_list"]] == ["PT Banten"]
    assert len(auth_client.get(reverse("contacts:list"), {"province": "99"}).context["object_list"]) == 2  # unknown: no filter
    key = create_key(organization=organization, user=user, name="Toko online")[1]
    listed = client.get(reverse("api:contacts"), {"province": "34"}, HTTP_AUTHORIZATION=f"Bearer {key}").json()["results"]
    assert [(c["name"], c["region_codes"]) for c in listed] == [
        ("CV Jogja", {"province": "34", "regency": "34.04", "district": "34.04.07", "village": CATURTUNGGAL}),
    ]
    assert client.get(reverse("api:contacts"), {"province": "99"}, HTTP_AUTHORIZATION=f"Bearer {key}").status_code == 400
