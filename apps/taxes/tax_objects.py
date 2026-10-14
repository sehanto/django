"""Tax object codes (kode objek pajak) for bukti potong, from DJP"s Coretax converters (sheet \"REF\"):
\"BPPU Excel to XML v.3\" for PPh 23 and PPh 4(2) with their rates, and \"BP21 Excel to XML v.4\" for PPh 21
on fees to non-employees (50% of gross at the Art. 17 rates). Official names; generated, don"t edit by hand."""

from decimal import Decimal

from .domain import PayerWithholding

PPH_23 = {
    "24-100-01": ("Hadiah, Penghargaan, Bonus dan Lainnya Selain yang Telah Dipotong PPh Pasal 21 Ayat (1) Huruf E UU PPh", Decimal("15")),
    "24-100-02": ("Sewa dan Penghasilan Lain Sehubungan Dengan Penggunaan Harta Kecuali Sewa Tanah dan/atau Bangunan yang Telah Dikenai PPh Pasal 4 Ayat (2) UU PPh.", Decimal("2")),
    "24-101-01": ("Dividen", Decimal("15")),
    "24-102-01": ("Bunga Selain yang Dikenakan PPh Pasal 4 ayat (2)", Decimal("15")),
    "24-103-01": ("Royalti", Decimal("15")),
    "24-103-02": ("Royalti yang diterima atau diperoleh Wajib Pajak orang pribadi dalam negeri yang menerapkan penghitungan Pajak Penghasilan menggunakan Norma Penghitungan Penghasilan Neto", Decimal("6")),
    "24-104-01": ("Jasa Teknik", Decimal("2")),
    "24-104-02": ("Jasa Manajemen", Decimal("2")),
    "24-104-03": ("Jasa Konsultan", Decimal("2")),
    "24-104-04": ("Jasa Penilai (Appraisal)", Decimal("2")),
    "24-104-05": ("Jasa Aktuaris", Decimal("2")),
    "24-104-06": ("Jasa Akuntansi, Pembukuan, dan Atestasi Laporan Keuangan", Decimal("2")),
    "24-104-07": ("Jasa Hukum", Decimal("2")),
    "24-104-08": ("Jasa Arsitektur", Decimal("2")),
    "24-104-09": ("Jasa Perencanaan Kota dan Arsitektur Landscape", Decimal("2")),
    "24-104-10": ("Jasa Perancang (Design)", Decimal("2")),
    "24-104-11": ("Jasa Pengeboran (Drilling) di Bidang Penambangan Minyak dan Gas Bumi (Migas) Kecuali yang Dilakukan oleh Badan Usaha Tetap (BUT)", Decimal("2")),
    "24-104-12": ("Jasa Penunjang di Bidang Usaha Panas Bumi dan Penambangan Minyak dan Gas Bumi (Migas)", Decimal("2")),
    "24-104-13": ("Jasa Penambangan dan Jasa Penunjang Selain di Bidang Usaha Panas Bumi dan Penambangan Minyak dan Gas Bumi (Migas)", Decimal("2")),
    "24-104-14": ("Jasa Penunjang di Bidang Penerbangan dan Bandar Udara", Decimal("2")),
    "24-104-15": ("Jasa Penebangan Hutan", Decimal("2")),
    "24-104-16": ("Jasa Pengolahan Limbah", Decimal("2")),
    "24-104-17": ("Jasa Penyedia Tenaga Kerja dan/atau Tenaga Ahli (Outsourcing Services)", Decimal("2")),
    "24-104-18": ("Jasa Perantara dan/atau Keagenan", Decimal("2")),
    "24-104-19": ("Jasa Bidang Perdagangan Surat-Surat Berharga, Kecuali yang Dilakukan Bursa Efek, Kustodian Sentral Efek Indonesia (KSEI) dan Kliring Penjaminan Efek Indonesia (KPEI)", Decimal("2")),
    "24-104-20": ("Jasa Kustodian/Penyimpanan/Penitipan, Kecuali yang Dilakukan Oleh KSEI", Decimal("2")),
    "24-104-21": ("Jasa Pengisian Suara (Dubbing) dan/atau Sulih Suara", Decimal("2")),
    "24-104-22": ("Jasa Mixing Film", Decimal("2")),
    "24-104-23": ("Jasa Pembuatan Sarana Promosi Film, Iklan, Poster, Foto, Slide, Klise, Banner, Pamphlet, Baliho dan Folder", Decimal("2")),
    "24-104-24": ("Jasa Sehubungan Dengan Software Atau Hardware Atau Sistem Komputer, Termasuk Perawatan, Pemeliharaan dan Perbaikan.", Decimal("2")),
    "24-104-25": ("Jasa Pembuatan dan/atau Pengelolaan Website", Decimal("2")),
    "24-104-26": ("Jasa Internet Termasuk Sambungannya", Decimal("2")),
    "24-104-27": ("Jasa Penyimpanan, Pengolahan dan/atau Penyaluran Data, Informasi, dan/atau Program", Decimal("2")),
    "24-104-28": ("Jasa Instalasi/Pemasangan Mesin, Peralatan, Listrik, Telepon, Air, Gas, Ac dan/atau Tv Kabel, Selain Yang Dilakukan Oleh Wajib Pajak Yang Ruang Lingkupnya Di Bidang Konstruksi dan Mempunyai Izin dan/atau Sertifikasi Sebagai Pengusaha Konstruksi", Decimal("2")),
    "24-104-29": ("Jasa Perawatan/Perbaikan/Pemeliharaan Mesin, Peralatan, Listrik, Telepon, Air, Gas, Ac dan/atau Tv Kabel, Selain Yang Dilakukan Oleh Wajib Pajak Yang Ruang Lingkupnya di Bidang Konstruksi dan Mempunyai Izin dan/atau Sertifikasi Sebagai Pengusaha Konstruksi", Decimal("2")),
    "24-104-30": ("Jasa Perawatan Kendaraan dan/atau Alat Transportasi Darat, Laut dan Udara", Decimal("2")),
    "24-104-31": ("Jasa Maklon", Decimal("2")),
    "24-104-32": ("Jasa Penyelidikan dan Keamanan", Decimal("2")),
    "24-104-33": ("Jasa Penyelenggara Kegiatan Atau Event Organizer", Decimal("2")),
    "24-104-34": ("Jasa Penyediaan Tempat dan/atau Waktu Dalam Media Massa, Media Luar Ruang Atau Media Lain Untuk Penyampaian Informasi, dan/atau Jasa Periklanan", Decimal("2")),
    "24-104-35": ("Jasa Pembasmian Hama", Decimal("2")),
    "24-104-36": ("Jasa Kebersihan Atau Cleaning Service", Decimal("2")),
    "24-104-37": ("Jasa Sedot Septic Tank", Decimal("2")),
    "24-104-38": ("Jasa Pemeliharaan Kolam", Decimal("2")),
    "24-104-39": ("Jasa Katering Atau Tata Boga", Decimal("2")),
    "24-104-40": ("Jasa Freight Forwarding", Decimal("2")),
    "24-104-41": ("Jasa Logistik", Decimal("2")),
    "24-104-42": ("Jasa Pengurusan Dokumen", Decimal("2")),
    "24-104-43": ("Jasa Pengepakan", Decimal("2")),
    "24-104-44": ("Jasa Loading dan Unloading", Decimal("2")),
    "24-104-45": ("Jasa Laboratorium dan/atau Pengujian Kecuali yang Dilakukan oleh Lembaga atau Institusi Pendidikan Dalam Rangka Penelitian Akademis", Decimal("2")),
    "24-104-46": ("Jasa Pengelolaan Parkir", Decimal("2")),
    "24-104-47": ("Jasa Penyondiran Tanah", Decimal("2")),
    "24-104-48": ("Jasa Penyiapan dan/atau Pengolahan Lahan", Decimal("2")),
    "24-104-49": ("Jasa Pembibitan dan/atau Penanaman Bibit", Decimal("2")),
    "24-104-50": ("Jasa Pemeliharaan Tanaman", Decimal("2")),
    "24-104-51": ("Jasa Pemanenan", Decimal("2")),
    "24-104-52": ("Jasa Pengolahan Hasil Pertanian, Perkebunan, Perikanan, Peternakan dan/atau Perhutanan", Decimal("2")),
    "24-104-53": ("Jasa Dekorasi", Decimal("2")),
    "24-104-54": ("Jasa Pencetakan/Penerbitan", Decimal("2")),
    "24-104-55": ("Jasa Penerjemahan", Decimal("2")),
    "24-104-56": ("Jasa Pengangkutan/Ekspedisi Kecuali Yang Telah Diatur Dalam Pasal 15 Undang-Undang Pajak Penghasilan", Decimal("2")),
    "24-104-57": ("Jasa Pelayanan Pelabuhan", Decimal("2")),
    "24-104-58": ("Jasa Pengangkutan Melalui Jalur Pipa", Decimal("2")),
    "24-104-59": ("Jasa Pengelolaan Penitipan Anak", Decimal("2")),
    "24-104-60": ("Jasa Pelatihan dan/atau Kursus", Decimal("2")),
    "24-104-61": ("Jasa Pengiriman dan Pengisian Uang Ke Atm", Decimal("2")),
    "24-104-62": ("Jasa Sertifikasi", Decimal("2")),
    "24-104-63": ("Jasa Survey", Decimal("2")),
    "24-104-64": ("Jasa Tester", Decimal("2")),
    "24-104-65": ("Jasa Selain Jasa-Jasa Tersebut di Atas yang Pembayarannya Dibebankan pada APBN (Anggaran Pendapatan dan Belanja Negara) Atau APBD (Anggaran Pendapatan dan Belanja Daerah).", Decimal("2")),
    "24-104-66": ("Jasa Penyelenggaraan Layanan Transaksi Pembayaran Terkait dengan Distribusi Token Oleh Penyelenggara Distribusi", Decimal("2")),
    "24-104-67": ("Jasa Pemasaran dengan Media Voucer Oleh Penyelenggara Voucer", Decimal("2")),
    "24-104-68": ("Jasa Penyelenggaraan Layanan Transaksi Pembayaran Terkait dengan Distribusi Voucer Oleh Penyelenggara Voucer dan Penyelenggara Distribusi", Decimal("2")),
    "24-104-69": ("Jasa Penyelenggaraan Program Loyalitas dan Penghargaan Pelanggan (Consumer Loyalty/Reward Program) Oleh Penyelenggara Voucer", Decimal("2")),
    "24-105-01": ("Bunga Pinjaman yang Diterima Wajib Pajak Dalam Negeri dan Bentuk Usaha Tetap Melalui Layanan Pinjam Meminjam Uang Berbasis Teknologi Informasi", Decimal("15")),
    "24-106-01": ("PPh pasal 23 yang terutang atas penghasilan Kontraktor Utama sehubungan pelaksanaan proyek pemerintah yang dibiayai Hibah atau Pinjaman Luar Negeri", Decimal("0")),
}
PPH_4_2 = {
    "28-402-01": ("Pengalihan Hak atas Tanah dan/atau Bangunan", Decimal("2.5")),
    "28-402-02": ("Pengalihan Rumah Sederhana dan Rumah Susun Sederhana yang Dilakukan oleh WP yang Usaha Pokoknya Mengalihkan Hak atas Tanah dan/atau Bangunan", Decimal("1")),
    "28-402-03": ("Pengalihan Hak atas Tanah dan/atau Bangunan kepada Pemerintah, BUMN yang Mendapat Penugasan Khusus dari Pemerintah, atau BUMD yang Mendapat Penugasan Khusus dari Kepala Daerah, sesuai UU mengenai Pengadaan Tanah bagi Pembangunan untuk Kepentingan Umum", Decimal("0")),
    "28-402-04": ("Pengalihan Hak atas Tanah dan/atau Bangunan sehubungan Pelaksanaan Proyek Pemerintah yang Dibiayai dengan Hibah atau Dana Pinjaman Luar Negeri", Decimal("2.5")),
    "28-403-02": ("Persewaan Tanah dan/atau Bangunan", Decimal("10")),
    "28-403-04": ("Persewaan Tanah dan/atau Bangunan sehubungan Pelaksanaan Proyek Pemerintah yang Dibiayai dengan Hibah atau Dana Pinjaman Luar Negeri", Decimal("10")),
    "28-404-01": ("Bunga Tabungan dan Bunga Deposito yang Ditempatkan di Dalam Negeri yang Dananya Bersumber Selain dari Devisa Hasil Ekspor (DHE)", Decimal("20")),
    "28-404-02": ("Bunga Deposito yang Ditempatkan di Dalam Negeri (mata uang IDR bersumber dari DHE tenor 1 bulan)", Decimal("7.5")),
    "28-404-03": ("Bunga Deposito yang Ditempatkan di Dalam Negeri (mata uang IDR bersumber dari DHE tenor 3 bulan)", Decimal("5")),
    "28-404-04": ("Bunga Deposito yang Ditempatkan di Dalam Negeri (mata uang IDR bersumber dari DHE tenor 6 bulan atau lebih)", Decimal("0")),
    "28-404-05": ("Bunga Deposito yang Ditempatkan di Dalam Negeri (mata uang USD bersumber dari DHE tenor 1 bulan)", Decimal("10")),
    "28-404-06": ("Bunga Deposito yang Ditempatkan di Dalam Negeri (mata uang USD bersumber dari DHE tenor 3 bulan)", Decimal("7.5")),
    "28-404-07": ("Bunga Deposito yang Ditempatkan di Dalam Negeri (mata uang USD bersumber dari DHE tenor 6 bulan)", Decimal("2.5")),
    "28-404-08": ("Bunga Deposito yang Ditempatkan di Dalam Negeri (mata uang USD bersumber dari DHE tenor lebih 6 bulan)", Decimal("0")),
    "28-404-09": ("Bunga Deposito/Tabungan yang Ditempatkan di Luar Negeri Melalui Bank yang Didirikan atau Bertempat Kedudukan di Indonesia atau Cabang Bank Luar Negeri di Indonesia", Decimal("20")),
    "28-404-10": ("Diskonto Sertifikat Bank Indonesia", Decimal("20")),
    "28-404-11": ("Jasa Giro", Decimal("20")),
    "28-404-12": ("Penghasilan dari Instrumen Moneter dan/atau Instrumen Keuangan Tertentu di Indonesia (Mata Uang VALAS Bersumber dari DHE SDA Tenor Lebih Dari 6 Bulan)", Decimal("0")),
    "28-404-13": ("Penghasilan dari Instrumen Moneter dan/atau Instrumen Keuangan Tertentu di Indonesia (Mata Uang VALAS Bersumber dari DHE SDA Tenor 6 Bulan)", Decimal("2.5")),
    "28-404-14": ("Penghasilan dari Instrumen Moneter dan/atau Instrumen Keuangan Tertentu di Indonesia (Mata Uang VALAS Bersumber dari DHE SDA Tenor Lebih Dari 3 Bulan sampai dengan Kurang Dari 6 Bulan)", Decimal("7.5")),
    "28-404-15": ("Penghasilan dari Instrumen Moneter dan/atau Instrumen Keuangan Tertentu di Indonesia (Mata Uang VALAS Bersumber dari DHE SDA Tenor 1 Bulan sampai dengan Kurang Dari 3 Bulan)", Decimal("10")),
    "28-404-16": ("Penghasilan dari Instrumen Moneter dan/atau Instrumen Keuangan Tertentu di Indonesia (Mata Uang IDR Bersumber dari DHE SDA Tenor 6 Bulan atau Lebih Dari 6 Bulan)", Decimal("0")),
    "28-404-17": ("Penghasilan dari Instrumen Moneter dan/atau Instrumen Keuangan Tertentu di Indonesia (Mata Uang VALAS Bersumber dari DHE SDA Tenor 3 Bulan sampai dengan Kurang Dari 6 Bulan)", Decimal("2.5")),
    "28-404-18": ("Penghasilan dari instrumen moneter dan/atau instrumen keuangan tertentu di Indonesia (mata uang IDR bersumber dari DHE SDA tenor 1 bulan sampai dengan kurang dari 3 bulan)", Decimal("5")),
    "28-409-22": ("Pekerjaan Konstruksi yang Dilakukan oleh Penyedia Jasa yang Memiliki Sertifikat Badan Usaha Kualifikasi Kecil atau Sertifikat Kompetensi Kerja untuk Usaha Orang Perseorangan", Decimal("1.75")),
    "28-409-23": ("Pekerjaan Konstruksi yang Dilakukan oleh Penyedia Jasa yang Tidak Memiliki Sertifikat Badan Usaha Atau Sertifikat Kompetensi Kerja untuk Usaha Orang Perseorangan", Decimal("4")),
    "28-409-24": ("Pekerjaan Konstruksi yang Dilakukan oleh Penyedia Jasa yang Memiliki Sertifikat Selain Sertifikat Badan Usaha Kualifikasi Kecil atau Sertifikat Kompetensi Kerja untuk Usaha Orang Perseorangan", Decimal("2.65")),
    "28-409-25": ("Pekerjaan Konstruksi Terintegrasi yang Dilakukan oleh Penyedia Jasa yang Memiliki Sertifikat Badan Usaha", Decimal("2.65")),
    "28-409-26": ("Pekerjaan Konstruksi Terintegrasi yang Dilakukan oleh Penyedia Jasa yang Tidak Memiliki Sertifikat Badan Usaha", Decimal("4")),
    "28-409-27": ("Jasa Konsultansi Konstruksi yang Dilakukan oleh Penyedia Jasa yang Memiliki Sertifikat Badan Usaha atau Sertifikat Kompetensi Kerja untuk Usaha Orang Perseorangan", Decimal("3.5")),
    "28-409-28": ("Jasa Konsultansi Konstruksi yang Dilakukan oleh Penyedia Jasa yang Tidak Memiliki Sertifikat Badan Usaha atau Sertifikat Kompetensi Kerja untuk Usaha Orang Perseorangan", Decimal("6")),
    "28-409-43": ("Pekerjaan Konstruksi yang Dilakukan oleh Penyedia Jasa yang Memiliki Sertifikat Badan Usaha Kualifikasi Kecil atau Sertifikat Kompetensi Kerja untuk Usaha Orang Perseorangan sehubungan Pelaksanaan Proyek Pemerintah yang Dibiayai dengan Hibah atau Dana Pinjaman Luar Negeri", Decimal("1.75")),
    "28-409-44": ("Pekerjaan Konstruksi yang Dilakukan oleh Penyedia Jasa yang Tidak Memiliki Sertifikat Badan Usaha Atau Sertifikat Kompetensi Kerja untuk Usaha Orang Perseorangan sehubungan Pelaksanaan Proyek Pemerintah yang Dibiayai dengan Hibah atau Dana Pinjaman Luar Negeri", Decimal("4")),
    "28-409-45": ("Pekerjaan Konstruksi yang Dilakukan oleh Penyedia Jasa yang Memiliki Sertifikat Selain Sertifikat Badan Usaha Kualifikasi Kecil atau Sertifikat Kompetensi Kerja untuk Usaha Orang Perseorangan sehubungan Pelaksanaan Proyek Pemerintah yang Dibiayai dengan Hibah atau Dana Pinjaman Luar Negeri", Decimal("2.65")),
    "28-409-46": ("Pekerjaan Konstruksi Terintegrasi yang Dilakukan oleh Penyedia Jasa yang Memiliki Sertifikat Badan Usaha sehubungan Pelaksanaan Proyek Pemerintah yang Dibiayai dengan Hibah atau Dana Pinjaman Luar Negeri", Decimal("2.65")),
    "28-409-47": ("Pekerjaan Konstruksi Terintegrasi yang Dilakukan oleh Penyedia Jasa yang Tidak Memiliki Sertifikat Badan Usaha sehubungan Pelaksanaan Proyek Pemerintah yang Dibiayai dengan Hibah atau Dana Pinjaman Luar Negeri", Decimal("4")),
    "28-409-48": ("Jasa Konsultansi Konstruksi yang Dilakukan oleh Penyedia Jasa yang Memiliki Sertifikat Badan Usaha atau Sertifikat Kompetensi Kerja untuk Usaha Orang Perseorangan sehubungan Pelaksanaan Proyek Pemerintah yang Dibiayai dengan Hibah atau Dana Pinjaman Luar Negeri", Decimal("3.5")),
    "28-409-49": ("Jasa Konsultansi Konstruksi yang Dilakukan oleh Penyedia Jasa yang Tidak Memiliki Sertifikat Badan Usaha atau Sertifikat Kompetensi Kerja untuk Usaha Orang Perseorangan sehubungan Pelaksanaan Proyek Pemerintah yang Dibiayai dengan Hibah atau Dana Pinjaman Luar Negeri", Decimal("6")),
    "28-409-50": ("Jasa Konstruksi Berupa Jasa Perencanaan Konstruksi (Dengan Kualifikasi Usaha) sehubungan Pelaksanaan Proyek Pemerintah yang Dibiayai dengan Hibah atau Dana Pinjaman Luar Negeri", Decimal("4")),
    "28-409-51": ("Jasa Konstruksi Berupa Jasa Perencanaan Konstruksi (Tanpa Kualifikasi Usaha) sehubungan Pelaksanaan Proyek Pemerintah yang Dibiayai dengan Hibah atau Dana Pinjaman Luar Negeri", Decimal("6")),
    "28-409-52": ("Jasa Konstruksi Berupa Jasa Pelaksanaan Konstruksi (Kualifikasi Usaha Kecil) sehubungan Pelaksanaan Proyek Pemerintah yang Dibiayai dengan Hibah atau Dana Pinjaman Luar Negeri", Decimal("2")),
    "28-409-53": ("Jasa Konstruksi Berupa Jasa Pelaksanaan Konstruksi (Kualifikasi Usaha Menengah dan Besar) sehubungan Pelaksanaan Proyek Pemerintah yang Dibiayai dengan Hibah atau Dana Pinjaman Luar Negeri", Decimal("3")),
    "28-409-54": ("Jasa Konstruksi Berupa Jasa Pelaksanaan Konstruksi (Tanpa Kualifikasi Usaha) sehubungan Pelaksanaan Proyek Pemerintah yang Dibiayai dengan Hibah atau Dana Pinjaman Luar Negeri", Decimal("4")),
    "28-409-55": ("Jasa Konstruksi Berupa Jasa Pengawasan Konstruksi (Dengan Kualifikasi Usaha) sehubungan Pelaksanaan Proyek Pemerintah yang Dibiayai dengan Hibah atau Dana Pinjaman Luar Negeri", Decimal("4")),
    "28-409-56": ("Jasa Konstruksi Berupa Jasa Pengawasan Konstruksi (Tanpa Kualifikasi Usaha) sehubungan Pelaksanaan Proyek Pemerintah yang Dibiayai dengan Hibah atau Dana Pinjaman Luar Negeri", Decimal("6")),
    "28-417-01": ("Bunga Simpanan yang Dibayarkan oleh Koperasi kepada Anggota Wajib Pajak Orang Pribadi (bunga sampai dengan Rp240.000,00)", Decimal("0")),
    "28-417-02": ("Bunga Simpanan yang Dibayarkan oleh Koperasi kepada Anggota Wajib Pajak Orang Pribadi (bunga di atas Rp240.000,00)", Decimal("10")),
    "28-419-01": ("Dividen yang Diterima/Diperoleh Wajib Pajak Orang Pribadi Dalam Negeri", Decimal("10")),
    "28-423-01": ("Pemotongan atau pemungutan PPh atas penjualan barang atau penyerahan jasa yang dilakukan oleh Wajib Pajak dengan peredaran bruto tertentu sesuai dengan Peraturan Pemerintah Nomor 23 Tahun 2018 atau Peraturan Pemerintah Nomor 55 Tahun 2022.", Decimal("0.5")),
    "28-423-02": ("Pemotongan atau pemungutan PPh atas transaksi pembelian yang dilakukan oleh Wajib Pajak dengan peredaran bruto tertentu sesuai dengan Peraturan Pemerintah Nomor 55 Tahun 2022.", Decimal("0.5")),
    "28-423-03": ("Pemotongan atau Pemungutan PPh atas transaksi penjualan barang atau penyerahan jasa yang dilakukan oleh Wajib Pajak orang pribadi yang memiliki peredaran bruto tertentu sesuai dengan PP 55 Tahun 2022 dengan peredaran usaha s.d Rp500.000.000,00.", Decimal("0.5")),
    "28-423-12": ("Pemotongan atau Pemungutan PPh atas transaksi penjualan barang atau penyerahan jasa yang dilakukan oleh Wajib Pajak yang memenuhi persyaratan tertentu untuk dikenai PPh yang bersifat final dengan tarif 0% di IKN (PPh UMKM di IKN).", Decimal("0")),
    "28-423-13": ("Pemotongan atau pemungutan PPh atas transaksi pembelian yang dilakukan oleh Wajib Pajak yang memanfaatkan fasilitas PPh final dengan tarif 0% di IKN (PPh UMKM di IKN).", Decimal("0")),
    "28-423-14": ("Pemotongan atau pemungutan PPh atas penjualan barang atau penyerahan jasa yang dilakukan oleh Wajib Pajak dengan peredaran bruto tertentu yang memperoleh penghasilan sehubungan pelaksanaan proyek pemerintah yang dibiayai Hibah atau Pinjaman Luar Negeri", Decimal("0.5")),
    "28-423-15": ("Pemotongan atau Pemungutan PPh atas transaksi penjualan barang atau penyerahan jasa yang dilakukan oleh Wajib Pajak yang memenuhi persyaratan tertentu untuk dikenai PPh yang bersifat final dengan tarif 0% di IKN yang memperoleh penghasilan sehubungan pelaksanaan proyek pemerintah yang dibiayai Hibah atau Pinjaman Luar Negeri", Decimal("0")),
}
NON_EMPLOYEE_PPH_21 = {
    "21-100-04": "Imbalan kepada Distributor Perusahaan Pemasaran Berjenjang atau Penjualan Langsung dan Kegiatan Sejenis Lainnya",
    "21-100-05": "Imbalan kepada Agen Asuransi",
    "21-100-06": "Imbalan kepada Petugas Penjaja Barang Dagangan",
    "21-100-07": "Imbalan kepada Tenaga Ahli (Pengacara, Akuntan, Arsitek, Dokter, Konsultan, Notaris, Pejabat Pembuat Akte Tanah, Penilai, Aktuaris)",
    "21-100-18": "Imbalan kepada Penasihat, Pengajar, Pelatih, Penceramah, Penyuluh, dan Moderator",
    "21-100-19": "Imbalan kepada Pengarang, Peneliti, Penerjemah",
    "21-100-20": "Imbalan kepada Pemberi Jasa dalam Segala Bidang",
    "21-100-21": "Imbalan kepada Agen Iklan",
    "21-100-22": "Imbalan kepada Pengawas atau Pengelola Proyek",
    "21-100-23": "Imbalan kepada Pembawa Pesanan atau yang Menemukan Langganan atau yang Menjadi Perantara",
    "21-100-33": "Imbalan kepada Pemain Musik, Pembawa Acara, Penyanyi, Pelawak, Bintang Film, Bintang Sinetron, Bintang Iklan, Sutradara, Kru Film, Foto Model, Peragawan/Peragawati, Pemain Drama, Penari, Pemahat, Pelukis, Pembuat/Pencipta Konten pada Media yang Dibagikan secara Daring (Influencer, Selebgram, Blogger, Vlogger, dan Sejenis Lainnya), dan Seniman Lainnya",
    "21-100-34": "Imbalan yang Diterima oleh Olahragawan",
}


# "BPSP Excel to XML v.3": final income tax deposited on one's own income (rent, construction).
SELF_DEPOSIT = {
    "28-403-02": ("Persewaan Tanah dan/atau Bangunan", Decimal("10")),
    "28-409-10": ("Jasa Konstruksi Berupa Jasa Pelaksanaan Konstruksi (Kualifikasi Usaha Kecil) sehubungan Pelaksanaan Proyek Pemerintah yang Dibiayai dengan Hibah atau Dana Pinjaman Luar Negeri", Decimal("2")),
    "28-409-10": ("Jasa Konstruksi Berupa Jasa Pelaksanaan Konstruksi (Kualifikasi Usaha Kecil) yang Disetor Sendiri", Decimal("2")),
    "28-409-11": ("Jasa Konstruksi Berupa Jasa Pelaksanaan Konstruksi (Kualifikasi Usaha Menengah dan Besar) sehubungan Pelaksanaan Proyek Pemerintah yang Dibiayai dengan Hibah atau Dana Pinjaman Luar Negeri", Decimal("3")),
    "28-409-22": ("Pekerjaan Konstruksi yang Dilakukan oleh Penyedia Jasa yang Memiliki Sertifikat Badan Usaha Kualifikasi Kecil atau Sertifikat Kompetensi Kerja untuk Usaha Orang Perseorangan (Disetor Sendiri)", Decimal("1.75")),
    "28-409-22": ("Pekerjaan Konstruksi yang Dilakukan oleh Penyedia Jasa yang Memiliki Sertifikat Badan Usaha Kualifikasi Kecil atau Sertifikat Kompetensi Kerja untuk Usaha Orang Perseorangan sehubungan Pelaksanaan Proyek Pemerintah yang Dibiayai dengan Hibah atau Dana Pinjaman Luar Negeri", Decimal("1.75")),
    "28-409-23": ("Pekerjaan Konstruksi yang Dilakukan oleh Penyedia Jasa yang Tidak Memiliki Sertifikat Badan Usaha Atau Sertifikat Kompetensi Kerja untuk Usaha Orang Perseorangan (Disetor Sendiri)", Decimal("4")),
    "28-409-23": ("Pekerjaan Konstruksi yang Dilakukan oleh Penyedia Jasa yang Tidak Memiliki Sertifikat Badan Usaha Atau Sertifikat Kompetensi Kerja untuk Usaha Orang Perseorangan sehubungan Pelaksanaan Proyek Pemerintah yang Dibiayai dengan Hibah atau Dana Pinjaman Luar Negeri", Decimal("4")),
    "28-409-24": ("Pekerjaan Konstruksi yang Dilakukan oleh Penyedia Jasa yang Memiliki Sertifikat Selain Sertifikat Badan Usaha Kualifikasi Kecil atau Sertifikat Kompetensi Kerja untuk Usaha Orang Perseorangan (Disetor Sendiri)", Decimal("2.65")),
    "28-409-24": ("Pekerjaan Konstruksi yang Dilakukan oleh Penyedia Jasa yang Memiliki Sertifikat Selain Sertifikat Badan Usaha Kualifikasi Kecil atau Sertifikat Kompetensi Kerja untuk Usaha Orang Perseorangan sehubungan Pelaksanaan Proyek Pemerintah yang Dibiayai dengan Hibah atau Dana Pinjaman Luar Negeri", Decimal("2.65")),
    "28-409-25": ("Pekerjaan Konstruksi Terintegrasi yang Dilakukan oleh Penyedia Jasa yang Memiliki Sertifikat Badan Usaha (Disetor Sendiri)", Decimal("2.65")),
    "28-409-25": ("Pekerjaan Konstruksi Terintegrasi yang Dilakukan oleh Penyedia Jasa yang Memiliki Sertifikat Badan Usaha sehubungan Pelaksanaan Proyek Pemerintah yang Dibiayai dengan Hibah atau Dana Pinjaman Luar Negeri", Decimal("2.65")),
    "28-409-26": ("Pekerjaan Konstruksi Terintegrasi yang Dilakukan oleh Penyedia Jasa yang Tidak Memiliki Sertifikat Badan Usaha (Disetor Sendiri)", Decimal("4")),
    "28-409-26": ("Pekerjaan Konstruksi Terintegrasi yang Dilakukan oleh Penyedia Jasa yang Tidak Memiliki Sertifikat Badan Usaha sehubungan Pelaksanaan Proyek Pemerintah yang Dibiayai dengan Hibah atau Dana Pinjaman Luar Negeri", Decimal("4")),
    "28-409-27": ("Jasa Konsultansi Konstruksi yang Dilakukan oleh Penyedia Jasa yang Memiliki Sertifikat Badan Usaha atau Sertifikat Kompetensi Kerja untuk Usaha Orang Perseorangan (Disetor Sendiri)", Decimal("3.5")),
    "28-409-27": ("Jasa Konsultansi Konstruksi yang Dilakukan oleh Penyedia Jasa yang Memiliki Sertifikat Badan Usaha atau Sertifikat Kompetensi Kerja untuk Usaha Orang Perseorangan sehubungan Pelaksanaan Proyek Pemerintah yang Dibiayai dengan Hibah atau Dana Pinjaman Luar Negeri", Decimal("3.5")),
    "28-409-28": ("Jasa Konsultansi Konstruksi yang Dilakukan oleh Penyedia Jasa yang Tidak Memiliki Sertifikat Badan Usaha atau Sertifikat Kompetensi Kerja untuk Usaha Orang Perseorangan (Disetor Sendiri)", Decimal("6")),
    "28-409-28": ("Jasa Konsultansi Konstruksi yang Dilakukan oleh Penyedia Jasa yang Tidak Memiliki Sertifikat Badan Usaha atau Sertifikat Kompetensi Kerja untuk Usaha Orang Perseorangan sehubungan Pelaksanaan Proyek Pemerintah yang Dibiayai dengan Hibah atau Dana Pinjaman Luar Negeri", Decimal("6")),
}
# "BPNR Excel to XML v.2": PPh 26 on payments to non-residents other than employees: (name, deemed %, rate %).
PPH_26 = {
    "27-100-01": ("Sewa dan Penghasilan Lain Sehubungan dengan Penggunaan Harta (PPh Pasal 26)", Decimal("100"), Decimal("20")),
    "27-100-02": ("Hadiah dan Penghargaan (PPh Pasal 26)", Decimal("100"), Decimal("20")),
    "27-100-03": ("Pensiun dan pembayaran berkala lainnya (PPh Pasal 26)", Decimal("100"), Decimal("20")),
    "27-100-04": ("Keuntungan Karena Pembebasan Utang (PPh Pasal 26)", Decimal("100"), Decimal("20")),
    "27-100-05": ("Penghasilan dari Penjualan atau Pengalihan Harta di Indonesia (PPh Pasal 26)", Decimal("25"), Decimal("20")),
    "27-100-07": ("Penghasilan dari Penjualan/Pengalihan Saham (PPh Pasal 26)", Decimal("100"), Decimal("20")),
    "27-100-08": ("Penghasilan dari Penjualan/Pengalihan Saham tidak di Bursa Efek (PPh Pasal 26)", Decimal("25"), Decimal("20")),
    "27-100-09": ("Penjualan atau Pengalihan Saham Perusahaan Antara (Conduit Company atau Special Purpose Company)(PPh Pasal 26)", Decimal("25"), Decimal("20")),
    "27-100-10": ("Sewa dan Penghasilan Lain Sehubungan dengan Penggunaan Harta Sehubungan Pelaksanaan Proyek Pemerintah yang Dibiayai Hibah atau Pinjaman Luar Negeri (PPh Pasal 26)", Decimal("100"), Decimal("20")),
    "27-100-11": ("Penghasilan dari Penjualan atau Pengalihan Harta di Indonesia sehubungan pelaksanaan proyek pemerintah yang dibiayai Hibah atau Pinjaman Luar Negeri (PPh Pasal 26)", Decimal("100"), Decimal("20")),
    "27-101-01": ("Dividen (PPh Pasal 26)", Decimal("100"), Decimal("20")),
    "27-102-01": ("Bunga Selain Bunga Obligasi (PPh Pasal 26)", Decimal("100"), Decimal("20")),
    "27-102-02": ("Premi Swap dan Transaksi Lindung Nilai Lainnya (PPh Pasal 26)", Decimal("100"), Decimal("20")),
    "27-102-03": ("Bunga Obligasi (PPh Pasal 26)", Decimal("100"), Decimal("10")),
    "27-103-01": ("Royalti (PPh Pasal 26)", Decimal("100"), Decimal("20")),
    "27-103-02": ("Royalti dalam rangka pelaksanaan proyek pemerintah yang dibiayai Hibah atau Pinjaman Luar Negeri (PPh Pasal 26)", Decimal("100"), Decimal("20")),
    "27-104-01": ("Imbalan Sehubungan dengan Jasa, Pekerjaan dan Kegiatan", Decimal("100"), Decimal("20")),
    "27-104-02": ("Imbalan Sehubungan dengan Jasa, Pekerjaan dan Kegiatan dalam rangka pelaksanaan proyek pemerintah yang dibiayai Hibah atau Pinjaman Luar Negeri (PPh Pasal 26)", Decimal("100"), Decimal("20")),
    "27-107-01": ("Bunga Pinjaman yang Diterima Wajib Pajak Dalam Negeri dan Bentuk Usaha Tetap Melalui Layanan Pinjam Meminjam Uang Berbasis Teknologi Informasi (PPh Pasal 26)", Decimal("100"), Decimal("20")),
}


def codes_for(kind: str) -> dict:
    """{code: name} for a withholding kind (``PayerWithholding`` value)."""
    if kind == PayerWithholding.PPH_21:
        return NON_EMPLOYEE_PPH_21
    if kind == PayerWithholding.PPH_26:
        return {code: name for code, (name, _deemed, _rate) in PPH_26.items()}
    table = {PayerWithholding.PPH_23: PPH_23, PayerWithholding.PPH_4_2: PPH_4_2}.get(kind, {})
    return {code: name for code, (name, _rate) in table.items()}


def reference_rate(kind: str, code: str) -> Decimal | None:
    """The rate (%) DJP's reference gives the code (Coretax checks it); None for PPh 21 fees."""
    if kind == PayerWithholding.PPH_26:
        return PPH_26[code][2] if code in PPH_26 else None
    entry = {PayerWithholding.PPH_23: PPH_23, PayerWithholding.PPH_4_2: PPH_4_2}.get(kind, {}).get(code)
    return entry[1] if entry else None


def deemed(kind: str, code: str) -> Decimal:
    """The share (%) of the gross the tax is on (PPh 26 codes; 100 otherwise)."""
    return PPH_26[code][1] if kind == PayerWithholding.PPH_26 and code in PPH_26 else Decimal("100")
