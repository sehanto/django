"""Tax deadlines: the term PP 55/2022 allows the MSME final income tax (Art. 59) and when a
month's tax must be deposited (PMK 81/2024)."""

from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse

from apps.ledger.services import JournalLineInput, create_journal_entry
from apps.taxes.domain import PPH_FINAL_TERM_YEARS, deposit_due, pph_final_last_year
from apps.tenants.forms import OrganizationSettingsForm
from apps.tenants.models import Organization

D = Decimal


@pytest.mark.parametrize(("year", "month", "due"), [
    (2026, 1, date(2026, 2, 16)),  # the 15th is a Sunday
    (2026, 4, date(2026, 5, 15)),  # a Friday
    (2026, 7, date(2026, 8, 17)),  # a Saturday (17 August is a holiday too, which isn't known here)
    (2025, 12, date(2026, 1, 15)),  # into the next year
])
def test_tax_is_deposited_by_the_15th_of_the_next_month_or_the_next_working_day(year, month, due):
    assert deposit_due(year, month) == due


def test_pp_55_allows_the_scheme_for_some_tax_years_per_legal_form():
    assert set(PPH_FINAL_TERM_YEARS) == set(Organization.LegalForm.values)
    assert pph_final_last_year(Organization.LegalForm.PERORANGAN, 2022) == 2028  # 7 tax years
    assert pph_final_last_year(Organization.LegalForm.CV, 2023) == 2026  # 4
    assert pph_final_last_year(Organization.LegalForm.PT_PERORANGAN, 2023) == 2026  # 4
    assert pph_final_last_year(Organization.LegalForm.PT, 2024) == 2026  # 3
    assert pph_final_last_year(Organization.LegalForm.PERORANGAN, 2015) == 2024  # counted from 2018 (Art. 59(3))


def pph_final_page(auth_client, year):
    return auth_client.get(reverse("taxes:pph_final"), {"year": year})


def test_the_pph_final_page_warns_when_the_term_ends(auth_client, organization):
    organization.legal_form, organization.pph_final_since = Organization.LegalForm.PT, 2024
    organization.save()
    assert pph_final_page(auth_client, 2025).context["last_year"] == 2026
    assert b"alert-warning" in pph_final_page(auth_client, 2026).content  # its last year
    assert b"alert-danger" in pph_final_page(auth_client, 2027).content  # beyond: regular income tax
    assert b"alert-danger" not in pph_final_page(auth_client, 2025).content


def test_the_pph_final_page_asks_for_the_first_year(auth_client, organization):
    assert pph_final_page(auth_client, 2026).context["last_year"] is None
    assert reverse("tenants:settings").encode() in pph_final_page(auth_client, 2026).content


def test_the_first_year_is_not_in_the_future_nor_before_2018(organization, user):
    def errors(year):
        form = OrganizationSettingsForm(
            {"name": organization.name, "legal_form": organization.legal_form, "uses_pph_final": "on", "pph_final_since": year,
             "inventory_cost_formula": organization.inventory_cost_formula, "depreciation_method": organization.depreciation_method},
            instance=organization, acting_user=user,
        )
        form.is_valid()
        return form.errors.get("pph_final_since")

    assert errors(2020) is None and errors(2017) and errors(date.today().year + 1)


def test_an_unpaid_month_past_its_due_date_is_overdue(auth_client, organization, account):
    organization.legal_form = Organization.LegalForm.CV
    organization.save(update_fields=["legal_form"])
    create_journal_entry(organization=organization, date=date(2026, 1, 10), description="Penjualan tunai", lines=[
        JournalLineInput(account("1-1000"), debit=D("2000000")), JournalLineInput(account("4-1000"), credit=D("2000000"))])
    january = pph_final_page(auth_client, 2026).context["rows"][0]
    assert january.due_on == date(2026, 2, 16) and january.overdue  # today is after it
    february = pph_final_page(auth_client, 2026).context["rows"][1]
    assert not february.overdue  # no tax that month
