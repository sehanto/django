"""All web addresses are in English (the interface language is chosen separately)."""

import re

from django.urls import URLPattern, URLResolver, get_resolver, reverse

INDONESIAN_SEGMENTS = {
    "akun", "masuk", "keluar", "daftar", "profil", "kata-sandi", "bahasa", "organisasi", "pengaturan", "baru",
    "pilih", "anggota", "hapus", "paket", "buku-besar", "jurnal", "ubah", "posting", "balik", "kontak", "pajak",
    "tarif", "setor", "batal", "transaksi", "penjualan", "pembelian", "pembayaran", "barang-jasa", "cetak",
    "terbitkan", "bayar", "laporan", "neraca-saldo", "laba-rugi", "posisi-keuangan", "umur", "ppn",
}


def iter_routes(patterns, prefix=""):
    for pattern in patterns:
        route = prefix + str(pattern.pattern)
        if isinstance(pattern, URLResolver):
            if route.startswith("admin/"):
                continue  # Django admin's own URLs
            yield from iter_routes(pattern.url_patterns, route)
        elif isinstance(pattern, URLPattern):
            yield route


def test_no_indonesian_path_segments():
    offending = [
        route for route in iter_routes(get_resolver().url_patterns)
        if INDONESIAN_SEGMENTS & set(re.split(r"[/<>:]", route))
    ]
    assert not offending, offending


def test_representative_english_urls():
    assert reverse("accounts:login") == "/account/login/"
    assert reverse("invoicing:list", kwargs={"kind": "sales"}) == "/sales/"
    assert reverse("invoicing:create", kwargs={"kind": "purchase"}) == "/purchases/new/"
    assert reverse("invoicing:pay", kwargs={"kind": "sales", "pk": 7}) == "/sales/7/pay/"
    assert reverse("ledger:journal_reverse", args=[3]) == "/ledger/journal/3/reverse/"
    assert reverse("reports:aging", kwargs={"kind": "purchase"}) == "/reports/aging/purchases/"
    assert reverse("taxes:pph_final_pay") == "/taxes/pph-final/pay/"
