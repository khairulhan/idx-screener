"""
Pengambil data harian untuk IDX Screener.

Urutan sumber:
  1. Ringkasan Saham resmi BEI (idx.co.id, endpoint GetStockSummary)
     -> lengkap: OHLC, nilai, frekuensi, foreign buy/sell.
  2. Cadangan: Yahoo Finance (yfinance) untuk daftar saham di tickers.txt
     -> tanpa frekuensi dan tanpa foreign flow.

Hasil:
  data/history/YYYYMMDD.csv  arsip harian (disimpan 60 hari terakhir)
  data/latest.csv            data terbaru + MA20 & rata-rata nilai 20 hari
  data/meta.json             tanggal bursa, sumber, waktu pengambilan
"""
import argparse
import csv
import datetime as dt
import json
import pathlib
import sys
import time
import zoneinfo

WIB = zoneinfo.ZoneInfo("Asia/Jakarta")
ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
HIST = DATA / "history"
SIMPAN_HARI = 60

KOLOM = ["Kode", "Nama", "Sebelumnya", "Open", "Tertinggi", "Terendah", "Penutupan",
         "Volume", "Nilai", "Frekuensi", "Foreign Buy Lbr", "Foreign Sell Lbr", "Net Foreign"]

IDX_URL = ("https://www.idx.co.id/primary/TradingSummary/GetStockSummary"
           "?length=9999&start=0&date={tgl}")


def log(*a):
    print(*a, file=sys.stderr, flush=True)


# ---------------------------------------------------------------- sumber 1: BEI
def ambil_idx(tgl: str) -> list[dict]:
    """Ambil Ringkasan Saham BEI untuk tanggal YYYYMMDD. Kosong = libur/belum rilis."""
    try:
        from curl_cffi import requests  # meniru sidik jari browser (lolos Cloudflare)
    except ImportError:
        log("curl_cffi belum terpasang; lewati sumber BEI")
        return []
    url = IDX_URL.format(tgl=tgl)
    for percobaan in range(3):
        try:
            r = requests.get(url, impersonate="chrome", timeout=60,
                             headers={"Referer": "https://www.idx.co.id/id/data-pasar/ringkasan-perdagangan/ringkasan-saham/"})
            if r.status_code == 200:
                return ubah_idx(r.json())
            log(f"BEI {tgl}: HTTP {r.status_code} (percobaan {percobaan + 1})")
        except Exception as e:  # noqa: BLE001
            log(f"BEI {tgl}: {e} (percobaan {percobaan + 1})")
        time.sleep(5 * (percobaan + 1))
    return []


def _f(d, *kunci):
    for k in kunci:
        if k in d and d[k] not in (None, ""):
            try:
                return float(d[k])
            except (TypeError, ValueError):
                pass
    return None


def ubah_idx(js) -> list[dict]:
    """Ubah JSON GetStockSummary menjadi baris standar. Nama field dicek beberapa variasi."""
    data = js.get("data", js if isinstance(js, list) else []) if isinstance(js, (dict, list)) else []
    out = []
    for d in data:
        kode = (d.get("StockCode") or d.get("Code") or "").strip().upper()
        close = _f(d, "Close", "ClosePrice", "LastPrice")
        if not kode or not close:
            continue
        fb = _f(d, "ForeignBuy") or 0.0
        fs = _f(d, "ForeignSell") or 0.0
        out.append({
            "Kode": kode,
            "Nama": (d.get("StockName") or "").strip(),
            "Sebelumnya": _f(d, "Previous", "PrevPrice"),
            "Open": _f(d, "OpenPrice", "Open"),
            "Tertinggi": _f(d, "High", "HighPrice"),
            "Terendah": _f(d, "Low", "LowPrice"),
            "Penutupan": close,
            "Volume": _f(d, "Volume"),
            "Nilai": _f(d, "Value"),
            "Frekuensi": _f(d, "Frequency"),
            "Foreign Buy Lbr": fb,
            "Foreign Sell Lbr": fs,
            # BEI mencatat foreign buy/sell dalam LEMBAR -> dikonversi ke Rupiah (perkiraan)
            "Net Foreign": (fb - fs) * close,
        })
    return out


# ------------------------------------------------------- sumber 2: Yahoo (cadangan)
def ambil_yahoo(kode_list: list[str]) -> tuple[str, list[dict]]:
    try:
        import yfinance as yf
    except ImportError:
        log("yfinance belum terpasang; cadangan tidak tersedia")
        return "", []
    tick = [k + ".JK" for k in kode_list]
    df = yf.download(tick, period="3mo", interval="1d", group_by="ticker",
                     auto_adjust=False, progress=False, threads=True)
    if df is None or df.empty:
        return "", []
    tgl_terakhir = df.index.max()
    out = []
    for k, t in zip(kode_list, tick):
        try:
            s = df[t].dropna(subset=["Close"])
        except KeyError:
            continue
        if s.empty or s.index[-1] != tgl_terakhir:
            continue
        hari, prev = s.iloc[-1], (s.iloc[-2] if len(s) > 1 else None)
        close = float(hari["Close"])
        vol = float(hari["Volume"] or 0)
        akhir20 = s.tail(20)
        ma20 = float(akhir20["Close"].mean()) if len(akhir20) >= 10 else None
        avg_nilai = float((akhir20["Close"] * akhir20["Volume"]).mean()) if len(akhir20) >= 10 else None
        out.append({
            "Kode": k, "Nama": "",
            "Sebelumnya": float(prev["Close"]) if prev is not None else None,
            "Open": float(hari["Open"]), "Tertinggi": float(hari["High"]),
            "Terendah": float(hari["Low"]), "Penutupan": close, "Volume": vol,
            "Nilai": vol * close,          # perkiraan: volume x harga penutupan
            "Frekuensi": None,             # Yahoo tidak menyediakan
            "Foreign Buy Lbr": None, "Foreign Sell Lbr": None, "Net Foreign": None,
            # Yahoo menyediakan riwayat harga, jadi MA20 & rata-rata nilai dihitung langsung
            "MA20": round(ma20, 2) if ma20 else None,
            "AvgNilai20": round(avg_nilai) if avg_nilai else None,
        })
    return tgl_terakhir.strftime("%Y%m%d"), out


# ------------------------------------------------------------------ arsip & olah
def tulis_csv(path: pathlib.Path, rows: list[dict], kolom: list[str]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=kolom, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: angka(r.get(k)) for k in kolom})


def angka(v):
    """Tulis angka tanpa pemisah ribuan; bilangan bulat tanpa desimal, lainnya 2 desimal."""
    if v is None:
        return ""
    if isinstance(v, float):
        return int(v) if v.is_integer() or abs(v) >= 1000 else round(v, 2)
    return v


def baca_csv(path: pathlib.Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def hitung_rata2(rows: list[dict]) -> list[dict]:
    """Tambahkan MA20 (harga) dan AvgNilai20 dari arsip 20 hari bursa terakhir."""
    files = sorted(HIST.glob("*.csv"))[-20:]
    harga, nilai = {}, {}
    for fp in files:
        for r in baca_csv(fp):
            try:
                harga.setdefault(r["Kode"], []).append(float(r["Penutupan"]))
                if r.get("Nilai"):
                    nilai.setdefault(r["Kode"], []).append(float(r["Nilai"]))
            except (ValueError, KeyError):
                pass
    for r in rows:
        h, v = harga.get(r["Kode"], []), nilai.get(r["Kode"], [])
        if len(h) >= 10:
            r["MA20"] = round(sum(h) / len(h), 2)
        else:
            r.setdefault("MA20", None)        # pertahankan nilai dari Yahoo bila ada
        if len(v) >= 10:
            r["AvgNilai20"] = round(sum(v) / len(v))
        else:
            r.setdefault("AvgNilai20", None)
    return rows


def rapikan_arsip():
    files = sorted(HIST.glob("*.csv"))
    for fp in files[:-SIMPAN_HARI]:
        fp.unlink()


def daftar_kode() -> list[str]:
    f = ROOT / "tickers.txt"
    if f.exists():
        return [x.strip().upper() for x in f.read_text().split() if x.strip() and not x.startswith("#")]
    return []


def tanggal_mundur(mulai: dt.date, n: int):
    d = mulai
    keluar = 0
    while keluar < n:
        if d.weekday() < 5:
            yield d.strftime("%Y%m%d")
            keluar += 1
        d -= dt.timedelta(days=1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tanggal", help="YYYYMMDD (bawaan: hari ini WIB, mundur bila libur)")
    ap.add_argument("--isi-riwayat", type=int, default=0, help="isi arsip N hari bursa ke belakang")
    a = ap.parse_args()

    hari_ini = dt.datetime.now(WIB).date()

    if a.isi_riwayat:
        for tgl in tanggal_mundur(hari_ini, a.isi_riwayat):
            if (HIST / f"{tgl}.csv").exists():
                continue
            rows = ambil_idx(tgl)
            if rows:
                tulis_csv(HIST / f"{tgl}.csv", rows, KOLOM)
                log(f"arsip {tgl}: {len(rows)} saham")
            time.sleep(1.5)

    sumber, tgl_bursa, rows = "", "", []
    calon = [a.tanggal] if a.tanggal else list(tanggal_mundur(hari_ini, 5))
    for tgl in calon:
        rows = ambil_idx(tgl)
        if rows:
            sumber, tgl_bursa = "Ringkasan Saham BEI", tgl
            break
        time.sleep(1.5)

    if not rows:
        kode = daftar_kode()
        if not kode and (DATA / "latest.csv").exists():
            kode = [r["Kode"] for r in baca_csv(DATA / "latest.csv")]
        if kode:
            log("BEI gagal -> memakai cadangan Yahoo Finance")
            tgl_bursa, rows = ambil_yahoo(kode)
            sumber = "Yahoo Finance (cadangan, tanpa frekuensi & asing)"

    if not rows:
        log("GAGAL: tidak ada data dari sumber mana pun. Data lama tidak diubah.")
        sys.exit(1)

    tulis_csv(HIST / f"{tgl_bursa}.csv", rows, KOLOM)
    rapikan_arsip()
    rows = hitung_rata2(rows)
    tulis_csv(DATA / "latest.csv", rows, KOLOM + ["MA20", "AvgNilai20"])
    t = dt.datetime.strptime(tgl_bursa, "%Y%m%d")
    meta = {
        "tanggal_bursa": t.strftime("%d-%m-%Y"),
        "sumber": sumber,
        "jumlah_saham": len(rows),
        "diambil": dt.datetime.now(WIB).strftime("%d-%m-%Y %H:%M WIB"),
    }
    (DATA / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    log(f"OK {sumber} {tgl_bursa}: {len(rows)} saham")


if __name__ == "__main__":
    main()
