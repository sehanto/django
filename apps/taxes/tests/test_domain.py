from decimal import Decimal

from django.utils import translation

from apps.taxes.domain import Month, compute_pph_final, compute_vat

D = Decimal


def test_ppn_12_percent_with_dpp_11_12_is_effectively_11_percent():
    result = compute_vat(D("1000000"), D("12"), 11, 12)
    assert result.dpp == D("916666.67")
    assert result.tax == D("110000.00")


def test_ppn_luxury_full_dpp():
    assert compute_vat(D("1000000"), D("12")).tax == D("120000.00")


def test_ppn_is_rounded_down():
    assert compute_vat(D("999"), D("11")).tax == D("109.00")  # 109.89 → 109


def test_ppn_zero_base():
    assert compute_vat(D("0"), D("12")).tax == 0


def test_pph_final_corporate_taxes_every_rupiah():
    months = compute_pph_final([D("10000000")] * 12, is_individual=False)
    assert all(m.tax == D("50000.00") for m in months)


def test_pph_final_individual_threshold_consumed_chronologically():
    turnover = [D("200000000"), D("200000000"), D("200000000"), D("50000000")] + [D("0")] * 8
    months = compute_pph_final(turnover, is_individual=True)
    assert [m.tax for m in months[:4]] == [0, 0, D("500000.00"), D("250000.00")]
    assert months[2].exempt_turnover == D("100000000")
    assert months[2].taxable_turnover == D("100000000")


def test_pph_final_schedule_is_keyed_by_month():
    months = compute_pph_final([D("0")] * 12, is_individual=False)
    assert [m.month for m in months] == list(Month)
    assert months[2].month is Month.MARCH


def test_month_tax_period_and_localized_name():
    assert Month.MARCH.tax_period(2026) == "03/2026"
    with translation.override("id"):
        assert str(Month.MAY.label) == "Mei"
    with translation.override("en"):
        assert str(Month.MAY.label) == "May"
