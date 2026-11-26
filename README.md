# IDX Screener — dashboard saham yang update sendiri

Dashboard screener IHSG (BSJP, Scalping, Swing, Akumulasi Asing, Tren Berita) yang **mengambil data sendiri setiap hari bursa** memakai GitHub Actions, lalu ditayangkan gratis di GitHub Pages.

```
Senin–Jumat 16:47 WIB  ─►  GitHub Actions menjalankan scripts/fetch_idx.py
                           (Ringkasan Saham BEI → cadangan Yahoo Finance)
                           + scripts/fetch_news.py (RSS berita + sentimen)
                       ─►  hasil disimpan ke folder data/ di repo
Anda membuka dashboard ─►  index.html membaca data/latest.csv & data/news.json
```

> **Bukan rekomendasi investasi.** Skor hanya menyaring data dengan aturan teknis sederhana.

---

## Isi paket

| File | Fungsi |
|---|---|
| `index.html` | Dashboard. Otomatis membaca `data/latest.csv`, `data/meta.json`, `data/news.json`. Tetap bisa tempel data manual. |
| `scripts/fetch_idx.py` | Mengambil Ringkasan Saham BEI (OHLC, nilai, frekuensi, foreign). Bila ditolak, pakai Yahoo Finance. Menghitung MA20 & rata-rata nilai 20 hari dari arsip. |
| `scripts/fetch_news.py` | Mengambil judul berita dari RSS (`feeds.txt`), menandai emiten, memberi sentimen −2…+2. |
| `.github/workflows/update-data.yml` | Jadwal harian data saham (16:47 & 19:23 WIB, Senin–Jumat). |
| `.github/workflows/update-news.yml` | Jadwal berita tiap 2 jam saat jam bursa. |
| `feeds.txt` | Daftar RSS berita — boleh ditambah. |
| `tickers.txt` | Daftar saham untuk sumber cadangan Yahoo. |
| `requirements.txt` | Pustaka Python yang dipasang otomatis. |

---

## Setup (sekali saja, ±20 menit, cukup lewat browser)

### 1. Buat akun & repo
1. Daftar/masuk di **github.com**.
2. Klik **+ → New repository**. Nama: `idx-screener`. Pilih **Public** (GitHub Pages gratis untuk repo publik). Centang *Add a README file*. Klik **Create repository**.

### 2. Unggah file
1. Ekstrak zip paket ini di komputer.
2. Di halaman repo, klik **Add file → Upload files**, lalu **seret seluruh isi folder** (termasuk folder `.github`, `scripts`, `data`) ke kotak unggah. Klik **Commit changes**.
3. **Penting — folder `.github` sering tersembunyi.** Di Mac tekan `Cmd + Shift + .` di Finder; di Windows aktifkan *View → Hidden items*. Pastikan setelah unggah, di repo ada `.github/workflows/update-data.yml`.
   - Kalau tetap tidak bisa: klik **Add file → Create new file**, ketik nama `.github/workflows/update-data.yml`, tempel isinya, commit. Ulangi untuk `update-news.yml`.

### 3. Beri izin bot menyimpan data
**Settings → Actions → General → Workflow permissions → pilih "Read and write permissions" → Save.**

### 4. Jalankan pertama kali (isi riwayat)
1. Buka tab **Actions**. Jika muncul tombol hijau *I understand my workflows…*, klik.
2. Pilih **Update data saham → Run workflow**. Isi kolom *Isi arsip N hari* dengan **25** → **Run workflow**.
3. Tunggu ±3–5 menit sampai centang hijau. Folder `data/` kini berisi `latest.csv`, `meta.json`, `news.json`, dan `history/`.
   (Riwayat 20 hari dibutuhkan untuk MA20 dan "lonjakan nilai" pada strategi Swing & BSJP.)

### 5. Tayangkan dashboard
**Settings → Pages → Source: Deploy from a branch → Branch: `main`, folder `/ (root)` → Save.**
Setelah 1–2 menit alamatnya muncul di atas halaman, mis. `https://namaanda.github.io/idx-screener/`. Simpan ke bookmark / layar utama HP.

Selesai. Mulai sekarang data diperbarui sendiri setiap hari bursa.

---

## Opsional: sentimen berita dinilai Claude

Tanpa pengaturan apa pun, sentimen berita memakai **kamus kata kunci** (gratis, tapi kasar). Agar dinilai Claude:

1. Buat API key di **console.anthropic.com** (berbayar per pemakaian; untuk ±100 judul beberapa kali sehari biayanya kecil, cek harga terbaru di situs Anthropic).
2. Di repo: **Settings → Secrets and variables → Actions → New repository secret**. Name: `ANTHROPIC_API_KEY`, Secret: tempel key → **Add secret**.
3. (Opsional) model lain: tambahkan *variable*/secret `CLAUDE_MODEL`; bawaan `claude-haiku-5-5`.

API key tersimpan aman di Secrets dan **tidak pernah** masuk ke halaman dashboard.

---

## Mengubah jadwal

Buka `.github/workflows/update-data.yml` → ikon pensil. Baris `cron: "47 16 * * 1-5"` artinya menit 47, jam 16, Senin–Jumat, zona `Asia/Jakarta`.
- Hindari menit `00` — GitHub sering menunda jadwal di awal jam.
- GitHub **tidak menjamin tepat waktu**; keterlambatan beberapa menit sampai jam bisa terjadi. Jadwal kedua (19:23) adalah cadangan. Anda selalu bisa menekan **Run workflow** manual.

## Mengubah sumber berita
Edit `feeds.txt`: satu URL RSS per baris. Kata kunci Google News bisa diganti (spasi ditulis `+`).

---

## Jika ada masalah

| Gejala | Penyebab & solusi |
|---|---|
| Log: `BEI ...: HTTP 403` lalu "memakai cadangan Yahoo" | Situs BEI dilindungi Cloudflare dan kadang menolak server GitHub. Data tetap terisi dari Yahoo (tanpa frekuensi & asing). Biasanya pulih sendiri di hari berikutnya. |
| `GAGAL: tidak ada data` | Libur bursa, atau semua sumber menolak. Data lama tidak diubah. Jalankan ulang manual nanti. |
| Kolom kosong / angka aneh setelah lama berjalan | BEI mungkin mengganti nama field. Jalankan skrip di komputer, cetak satu baris JSON, sesuaikan `ubah_idx()` di `fetch_idx.py`. |
| Push gagal `permission denied` | Langkah 3 belum dilakukan. |
| Jadwal berhenti | GitHub dapat menonaktifkan jadwal pada repo yang lama tidak aktif. Buka tab Actions → pilih workflow → **Enable workflow**. |
| Dashboard masih menampilkan "DATA CONTOH" | `data/latest.csv` belum ada (langkah 4) atau Anda sedang memakai data tempelan — klik **Kembali ke data otomatis**. |

## Menjalankan di komputer sendiri (opsional)
```bash
pip install -r requirements.txt
python scripts/fetch_idx.py --isi-riwayat 25
python scripts/fetch_news.py
python -m http.server 8000     # buka http://localhost:8000
```

---

## Catatan penting
- **Data akhir hari, bukan real-time.** Ringkasan Saham BEI baru tersedia setelah penutupan. Untuk BSJP berbasis data Sesi I / jelang penutupan, perlu API intraday berbayar; ganti fungsi `ambil_idx()` dengan panggilan API tersebut (token simpan di Secrets).
- **Foreign buy/sell dari BEI dalam lembar saham.** Skrip mengonversinya ke Rupiah = (beli − jual) × harga penutupan, sehingga "Net Foreign" adalah **perkiraan**.
- **Data milik PT Bursa Efek Indonesia.** Gunakan untuk keperluan pribadi dan patuhi syarat penggunaan situs BEI; jangan dijual ulang.
- **Repo publik = dashboard & data bisa dilihat siapa saja yang tahu alamatnya.** API key tetap aman di Secrets.
- Tombol "Analisis berita dengan Claude" di panel Berita hanya aktif bila dashboard dibuka di Claude; di GitHub Pages, sentimen berasal dari workflow (kamus atau Claude API).
