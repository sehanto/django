"""Default chart of accounts for Indonesian MSMEs, structured per SAK EMKM.

Each tuple: (code, name, category, system_role, is_contra)
"""

from .domain import AccountCategory as C
from .domain import SystemRole as R

DEFAULT_CHART = [
    # Aset
    ("1-1000", "Kas", C.CASH_BANK, None, False),
    ("1-1100", "Bank", C.CASH_BANK, None, False),
    ("1-1200", "Piutang Usaha", C.RECEIVABLE, R.RECEIVABLE, False),
    ("1-1300", "Persediaan Barang Dagang", C.INVENTORY, None, False),
    ("1-1400", "PPN Masukan", C.OTHER_CURRENT_ASSET, R.INPUT_VAT, False),
    ("1-1500", "Pajak Dibayar di Muka", C.OTHER_CURRENT_ASSET, None, False),
    ("1-1600", "Biaya Dibayar di Muka", C.OTHER_CURRENT_ASSET, None, False),
    ("1-2000", "Tanah", C.FIXED_ASSET, None, False),
    ("1-2100", "Bangunan", C.FIXED_ASSET, None, False),
    ("1-2200", "Kendaraan", C.FIXED_ASSET, None, False),
    ("1-2300", "Peralatan Usaha", C.FIXED_ASSET, None, False),
    ("1-2900", "Akumulasi Penyusutan Aset Tetap", C.FIXED_ASSET, None, True),
    # Liabilitas
    ("2-1000", "Utang Usaha", C.CURRENT_LIABILITY, R.PAYABLE, False),
    ("2-1100", "PPN Keluaran", C.CURRENT_LIABILITY, R.OUTPUT_VAT, False),
    ("2-1150", "PPnBM Keluaran", C.CURRENT_LIABILITY, R.LUXURY_TAX, False),
    ("2-1200", "Utang PPh Final", C.CURRENT_LIABILITY, R.PPH_FINAL_PAYABLE, False),
    ("2-1210", "Utang PPh Pasal 23", C.CURRENT_LIABILITY, R.PPH_23_PAYABLE, False),
    ("2-1220", "Utang PPh Pasal 4 ayat (2)", C.CURRENT_LIABILITY, R.PPH_4_2_PAYABLE, False),
    ("2-1230", "Utang PPh Pasal 21", C.CURRENT_LIABILITY, R.PPH_21_PAYABLE, False),
    ("2-1240", "Utang Iuran BPJS", C.CURRENT_LIABILITY, R.BPJS_PAYABLE, False),
    ("2-1250", "Utang PPh Pasal 26", C.CURRENT_LIABILITY, R.PPH_26_PAYABLE, False),
    ("2-1300", "Utang Gaji", C.CURRENT_LIABILITY, None, False),
    ("2-1400", "Utang Lain-lain", C.CURRENT_LIABILITY, None, False),
    ("2-2000", "Utang Bank Jangka Panjang", C.LONG_TERM_LIABILITY, None, False),
    # Ekuitas
    ("3-1000", "Modal Pemilik", C.EQUITY, None, False),
    ("3-2000", "Prive", C.EQUITY, None, True),
    ("3-3000", "Saldo Laba", C.EQUITY, R.RETAINED_EARNINGS, False),
    # Pendapatan
    ("4-1000", "Pendapatan Penjualan", C.REVENUE, R.SALES_REVENUE, False),
    ("4-1100", "Pendapatan Jasa", C.REVENUE, None, False),
    ("4-1200", "Retur dan Potongan Penjualan", C.REVENUE, None, True),
    ("4-2000", "Pendapatan Lain-lain", C.OTHER_INCOME, None, False),
    ("4-2100", "Pendapatan Bunga Bank", C.OTHER_INCOME, None, False),
    # Beban
    ("5-1000", "Beban Pokok Penjualan", C.COST_OF_SALES, R.PURCHASE_EXPENSE, False),
    ("6-1000", "Beban Gaji dan Upah", C.OPERATING_EXPENSE, None, False),
    ("6-1100", "Beban Sewa", C.OPERATING_EXPENSE, None, False),
    ("6-1200", "Beban Listrik, Air, dan Telepon", C.OPERATING_EXPENSE, None, False),
    ("6-1300", "Beban Transportasi", C.OPERATING_EXPENSE, None, False),
    ("6-1400", "Beban Perlengkapan dan ATK", C.OPERATING_EXPENSE, None, False),
    ("6-1500", "Beban Pemasaran", C.OPERATING_EXPENSE, None, False),
    ("6-1600", "Beban Penyusutan", C.OPERATING_EXPENSE, None, False),
    ("6-1700", "Beban Perbaikan dan Pemeliharaan", C.OPERATING_EXPENSE, None, False),
    ("6-1900", "Beban Usaha Lain-lain", C.OPERATING_EXPENSE, None, False),
    ("7-1000", "Beban Administrasi Bank", C.OTHER_EXPENSE, None, False),
    ("7-1100", "Beban Bunga", C.OTHER_EXPENSE, None, False),
    ("8-1000", "Beban PPh Final", C.INCOME_TAX, R.PPH_FINAL_EXPENSE, False),
]
