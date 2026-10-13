"""PPN facilities on a Faktur Pajak (transaction codes 07 and 08).

The codes and their descriptions are DJP's reference data, copied from the sheets
"REF-KetTambahan" and "REF-CapFasilitas" of "Sample Faktur PK Template
v.1.6.1" (converter v1.6). They are official Indonesian texts and are not translated. The
same code means a different facility under 07 and under 08, so look codes up per
transaction code.
"""

import re

from django.db import models
from django.utils.translation import gettext_lazy as _


class VatFacility(models.TextChoices):
    """A delivery whose PPN the buyer doesn't pay: computed on the faktur, never charged."""

    NONE = "", _("No facility")
    NOT_COLLECTED = "07", _("PPN not collected or borne by the government (code 07)")
    EXEMPT = "08", _("PPN exempt (code 08)")


# Keterangan tambahan ("AddInfo" in the XML), per transaction code.
ADDITIONAL_INFO = {
    VatFacility.NOT_COLLECTED: (
        ("TD.00501", "1 - untuk Kawasan Bebas"),
        ("TD.00502", "2 - untuk Tempat Penimbunan Berikat"),
        ("TD.00503", "3 - untuk Hibah dan Bantuan Luar Negeri"),
        ("TD.00504", "4 - untuk Avtur"),
        ("TD.00505", "5 - untuk Lainnya"),
        ("TD.00506", "6 - untuk Kontraktor Perjanjian Karya Pengusahaan Pertambangan Batubara Generasi I"),
        ("TD.00507", "7 - untuk Penyerahan bahan bakar minyak untuk Kapal Angkutan Laut Luar Negeri"),
        ("TD.00508", "8 - untuk Penyerahan jasa kena pajak terkait alat angkutan tertentu"),
        ("TD.00509", "9 - untuk Penyerahan BKP Tertentu di KEK"),
        ("TD.00510", "10 - untuk BKP tertentu yang bersifat strategis berupa anode slime"),
        ("TD.00511", "11 - untuk Penyerahan alat angkutan tertentu dan/atau Jasa Kena Pajak terkait alat angkutan tertentu"),
        ("TD.00512", "12 - untuk Penyerahan kepada Kontraktor Kerja Sama Migas yang mengikuti ketentuan Peraturan Pemerintah Nomor 27 Tahun 2017"),
        ("TD.00513", "13 - Penyerahan Rumah Tapak dan Satuan Rumah Susun Rumah Susun Ditanggung Pemerintah Tahun Anggaran 2025"),
        ("TD.00514", "14 - Penyerahan Jasa Sewa Ruangan atau Bangunan Kepada Pedagang Eceran yang Ditanggung Pemerintah Tahun Anggaran 2021"),
        ("TD.00515", "15 - Penyerahan Barang dan Jasa Dalam Rangka Penanganan Pandemi COVID-19 (PMK 239/PMK. 03/2020)"),
        ("TD.00516", "16 - Insentif PMK-103/PMK.010/2021 berupa PPN atas Penyerahan Rumah Tapak dan Unit Hunian Rumah Susun yang Ditanggung Pemerintah Tahun Anggaran 2021"),
        ("TD.00517", "17 - Kawasan Ekonomi Khusus PP nomor 40 Tahun 2021"),
        ("TD.00518", "18 - Kawasan Bebas PP nomor 41 Tahun 2021"),
        ("TD.00519", "19 - Penyerahan Rumah Tapak dan Unit Hunian Rumah Susun yang Ditanggung Pemerintah Tahun Anggaran 2022"),
        ("TD.00520", "20 - PPN Ditanggung Pemerintah dalam rangka Penanganan Pandemi Corona Virus"),
        ("TD.00521", "21 - Penyerahan kepada Kontraktor Kerja Sama Migas yang mengikuti ketentuan Peraturan Pemerintah Nomor 53 Tahun 2017"),
        ("TD.00522", "22 - BKP strategis tertentu dalam bentuk anode slime dan emas butiran"),
        ("TD.00523", "23 - untuk penyerahan kertas koran dan/atau majalah"),
        ("TD.00524", "24 - PPN Ditanggung Pemerintah"),
        ("TD.00525", "25 - BKP dan JKP tertentu"),
        ("TD.00526", "26 - Penyerahan BKP dan JKP di Ibu Kota Negara baru"),
        ("TD.00527", "27 - Penyerahan kendaraan listrik berbasis baterai"),
        ("TD.00528", "28 - Insentif Tambahan Penyerahan Rumah Tapak dan Satuan Rumah Susun Rumah Susun Ditanggung Pemerintah Tahun Anggaran 2025"),
        ("TD.00529", "29 - PPN atas Penyerahan Hewan Khusus Tertentu Berupa Kuda serta Perlengkapan Pendukungnya Pemerintah Tahun Anggaran 2025"),
        ("TD.00530", "30 - PPN atas Penyerahan Bekal Khusus Operasi Tertentu Yang Ditanggung Pemerintah Tahun Anggaran 2025"),
        ("TD.00531", "31 - Penyerahan Rumah Tapak dan Satuan Rumah Susun Rumah Susun Ditanggung Pemerintah Tahun Anggaran 2026"),
    ),
    VatFacility.EXEMPT: (
        ("TD.00501", "1 - untuk BKP dan JKP Tertentu"),
        ("TD.00502", "2 - untuk BKP Tertentu yang Bersifat Strategis"),
        ("TD.00503", "3 - untuk Jasa Kebandarudaraan"),
        ("TD.00504", "4 - untuk Lainnya"),
        ("TD.00505", "5 - untuk BKP Tertentu yang Bersifat Strategis sesuai PP Nomor 81 Tahun 2015"),
        ("TD.00506", "6 - untuk Penyerahan Jasa Kepelabuhan Tertentu untuk kegiatan angkutan laut Luar Negeri"),
        ("TD.00507", "7 - untuk Penyerahan Air Bersih"),
        ("TD.00508", "8 - Penyerahan BKP tertentu yang bersifat strategis berdasarkan PP 48 Tahun 2020"),
        ("TD.00509", "9 - Penyerahan kepada Perwakilan Negara Asing dan Badan Internasional serta Pejabatnya"),
        ("TD.00510", "10 - BKP dan JKP tertentu"),
    ),
}

# Cap fasilitas ("FacilityStamp" in the XML), per transaction code.
STAMPS = {
    VatFacility.NOT_COLLECTED: (
        ("TD.01101", "1 - Pajak Pertambahan Nilai Tidak Dipungut berdasarkan PP Nomor 10 Tahun 2012"),
        ("TD.01102", "2 - Pajak Pertambahan Nilai atau Pajak Pertambahan Nilai dan Pajak Penjualan atas Barang Mewah tidak dipungut"),
        ("TD.01103", "3 - Pajak Pertambahan Nilai dan Pajak Penjualan atas Barang Mewah Tidak Dipungut"),
        ("TD.01104", "4 - Pajak Pertambahan Nilai Tidak Dipungut Sesuai PP Nomor 71 Tahun 2012"),
        ("TD.01105", "5 - (Tidak ada Cap)"),
        ("TD.01106", "6 - PPN dan/atau PPnBM tidak dipungut berdasarkan PMK No. 194/PMK.03/2012"),
        ("TD.01107", "7 - PPN Tidak Dipungut Berdasarkan PP Nomor 15 Tahun 2015"),
        ("TD.01108", "8 - PPN Tidak Dipungut Berdasarkan PP Nomor 69 Tahun 2015"),
        ("TD.01109", "9 - PPN Tidak Dipungut Berdasarkan PP Nomor 96 Tahun 2015"),
        ("TD.01110", "10 - PPN Tidak Dipungut Berdasarkan PP Nomor 106 Tahun 2015"),
        ("TD.01111", "11 - PPN Tidak Dipungut Sesuai PP Nomor 50 Tahun 2019"),
        ("TD.01112", "12 - PPN atau PPN dan PPnBM Tidak Dipungut Sesuai Dengan PP Nomor 27 Tahun 2017"),
        ("TD.01113", "13 - PPN DITANGGUNG PEMERINTAH EKSEKUSI PMK NOMOR 13 TAHUN 2025"),
        ("TD.01114", "14 - PPN DITANGGUNG PEMERINTAH EKS PMK 102/PMK.010/2021"),
        ("TD.01115", "15 - PPN DITANGGUNG PEMERINTAH EKS PMK 239/PMK.03/2020"),
        ("TD.01116", "16 - Insentif PPN DITANGGUNG PEMERINTAH EKSEKUSI PMK NOMOR 103/PMK.010/2021"),
        ("TD.01117", "17 - PAJAK PERTAMBAHAN NILAI TIDAK DIPUNGUT BERDASARKAN PP NOMOR 40 TAHUN 2021"),
        ("TD.01118", "18 - PAJAK PERTAMBAHAN NILAI TIDAK DIPUNGUT BERDASARKAN PP NOMOR 41 TAHUN 2021"),
        ("TD.01119", "19 - PPN DITANGGUNG PEMERINTAH EKS PMK 6/PMK.010/2022"),
        ("TD.01120", "20 - PPN DITANGGUNG PEMERINTAH EKSEKUSI PMK NOMOR 226/PMK.03/2021"),
        ("TD.01121", "21 - PPN ATAU PPN DAN PPnBM TIDAK DIPUNGUT SESUAI DENGAN PP NOMOR 53 TAHUN 2017"),
        ("TD.01122", "22 - PPN tidak dipungut berdasarkan PP Nomor 70 Tahun 2021"),
        ("TD.01123", "23 - PPN ditanggung Pemerintah Ex PMK-125/PMK.01/2020"),
        ("TD.01124", "24 - (Tidak ada Cap)"),
        ("TD.01125", "25 - PPN tidak dipungut berdasarkan PP Nomor 49 Tahun 2022"),
        ("TD.01126", "26 - PPN tidak dipungut berdasarkan PP Nomor 12 Tahun 2023"),
        ("TD.01127", "27 - PPN Ditanggung Pemerintah berdasarkan PMK Nomor 12 Tahun 2025"),
        ("TD.01128", "28 - PPN DITANGGUNG PEMERINTAH EKSEKUSI PMK NOMOR 60 TAHUN 2025"),
        ("TD.01129", "29 - PPN DITANGGUNG PEMERINTAH BERDASARKAN PMK NOMOR 61 TAHUN 2025"),
        ("TD.01130", "30 - PPN DITANGGUNG PEMERINTAH BERDASARKAN PMK NOMOR 44 TAHUN 2025"),
        ("TD.01131", "31 - PPN DITANGGUNG PEMERINTAH BERDASARKAN PMK NOMOR 90 TAHUN 2025"),
    ),
    VatFacility.EXEMPT: (
        ("TD.01101", "1 - PPN Dibebaskan Sesuai PP Nomor 146 Tahun 2000 Sebagaimana Telah Diubah Dengan PP Nomor 38 Tahun 2003"),
        ("TD.01102", "2 - PPN Dibebaskan Sesuai PP Nomor 12 Tahun 2001 Sebagaimana Telah Beberapa Kali Diubah Terakhir Dengan PP Nomor 31 Tahun 2007"),
        ("TD.01103", "3 - PPN dibebaskan berdasarkan Peraturan Pemerintah Nomor 28 Tahun 2009"),
        ("TD.01104", "4 - (Tidak ada cap)"),
        ("TD.01105", "5 - PPN Dibebaskan Sesuai Dengan PP Nomor 81 Tahun 2015"),
        ("TD.01106", "6 - PPN Dibebaskan Berdasarkan PP Nomor 74 Tahun 2015"),
        ("TD.01107", "7 - (tanpa cap)"),
        ("TD.01108", "8 - PPN DIBEBASKAN SESUAI PP NOMOR 81 TAHUN 2015 SEBAGAIMANA TELAH DIUBAH DENGAN PP 48 TAHUN 2020"),
        ("TD.01109", "9 - PPN DIBEBASKAN BERDASARKAN PP NOMOR 47 TAHUN 2020"),
        ("TD.01110", "10 - PPN Dibebaskan berdasarkan PP Nomor 49 Tahun 2022"),
    ),
}


def describe(table: dict, facility: str, code: str) -> str:
    """The DJP description of a code, or the code itself when it isn't in the list."""
    return dict(table.get(facility, ())).get(code, code)


def stamp_text(facility: str, code: str) -> str:
    """The stamp as printed on the faktur ("PPN TIDAK DIPUNGUT ..."); empty for DJP's
    "no stamp" entries."""
    text = re.sub(r"^\d+\s*-\s*", "", describe(STAMPS, facility, code))
    return "" if re.match(r"\((tidak ada|tanpa) cap\)", text, re.IGNORECASE) else text

