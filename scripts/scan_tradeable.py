"""
Pemindai "Tradeable" untuk IDX Screener.

Menerapkan hierarki analisis price action secara mekanis (hanya posisi beli):
  1. Konteks   : tren naik (harga > MA50, MA20 > MA50, MA50 menanjak 10 hari)
  2. Struktur  : higher high & higher low (zigzag 2,5 x ATR)
  3. Level     : pullback ke zona Fibonacci 38,2-61,8% / MA20, atau breakout resistance 60 hari
  4. Konfirmasi: candle penolakan / tutup kuat; breakout butuh volume >= 1,5x rata-rata
  5. Risiko    : stop di luar struktur, rasio untung-rugi >= 1:2

Semesta: saham di data/latest.csv dengan harga >= Rp50 dan rata-rata nilai 20 hari >= Rp5 M.
Data harga: OHLC harian 6 bulan dari Yahoo Finance (yfinance).

Hasil: data/tradeable.json

Jalankan:  python scripts/scan_tradeable.py
           python scripts/scan_tradeable.py --dari-pickle ohlc.pkl   (uji tanpa internet)
"""
import argparse
import csv
import datetime as dt
import json
import pathlib
import sys
import zoneinfo

import numpy as np
import pandas as pd

WIB = zoneinfo.ZoneInfo("Asia/Jakarta")
ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
HARGA_MIN, NILAI_MIN = 50, 5e9


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def ara(p):
    return 35 if p <= 200 else (25 if p <= 5000 else 20)


# ------------------------------------------------------------------ semesta
def semesta():
    f = DATA / "latest.csv"
    kode = []
    if f.exists():
        with open(f, encoding="utf-8", newline="") as fh:
            for r in csv.DictReader(fh):
                try:
                    if float(r["Penutupan"] or 0) >= HARGA_MIN and float(r.get("AvgNilai20") or 0) >= NILAI_MIN:
                        kode.append(r["Kode"].strip().upper())
                except ValueError:
                    pass
    simpan = DATA / "universe.txt"
    if len(kode) >= 60:          # data BEI lengkap -> perbarui daftar semesta
        simpan.write_text("\n".join(sorted(kode)), encoding="utf-8")
    elif simpan.exists():        # hari cadangan Yahoo (data tidak lengkap) -> pakai semesta terakhir
        kode = simpan.read_text(encoding="utf-8").split()
        log(f"latest.csv tidak lengkap; memakai semesta tersimpan ({len(kode)} saham)")
    return sorted(set(kode))


def unduh(kode):
    import yfinance as yf
    tick = [k + ".JK" for k in kode]
    out = {}
    for i in range(0, len(tick), 50):
        part = tick[i:i + 50]
        df = yf.download(part, period="6mo", interval="1d", group_by="ticker",
                         auto_adjust=False, progress=False, threads=True)
        for t in part:
            try:
                s = df[t][["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close"])
            except KeyError:
                continue
            s.columns = ["open", "high", "low", "close", "volume"]
            if len(s) >= 60:
                out[t[:-3]] = s.astype(float)
    return out


# ------------------------------------------------------------------ analisis
def indikator(df):
    c = df.close
    df = df.copy()
    df["ma20"], df["ma50"] = c.rolling(20).mean(), c.rolling(50).mean()
    tr = pd.concat([df.high - df.low, (df.high - c.shift()).abs(), (df.low - c.shift()).abs()], axis=1).max(axis=1)
    df["atr"] = tr.ewm(alpha=1 / 14, adjust=False).mean()
    df["vol20"] = df.volume.rolling(20).mean().shift()
    return df


def zigzag(df, k=2.5):
    hi, lo, atr = df.high.values, df.low.values, df.atr.values
    piv, mode, e = [], None, 0
    for i in range(1, len(df)):
        th = k * atr[i]
        if mode is None:
            if hi[i] - lo[e] >= th: piv.append(("L", e, lo[e])); mode, e = "up", i
            elif hi[e] - lo[i] >= th: piv.append(("H", e, hi[e])); mode, e = "dn", i
        elif mode == "up":
            if hi[i] >= hi[e]: e = i
            elif hi[e] - lo[i] >= th: piv.append(("H", e, hi[e])); mode, e = "dn", i
        else:
            if lo[i] <= lo[e]: e = i
            elif hi[i] - lo[e] >= th: piv.append(("L", e, lo[e])); mode, e = "up", i
    return piv, mode, e


def bulat(x):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return None
    x = float(x)
    return int(round(x)) if abs(x) >= 100 else round(x, 2)


def nilai_saham(kode, raw):
    df = indikator(raw)
    t, y = df.iloc[-1], df.iloc[-2]
    c, a = t.close, t.atr
    rng = max(t.high - t.low, 1e-9)
    pos = (c - t.low) / rng
    chg = (c / y.close - 1) * 100
    res60 = df.high.iloc[-61:-1].max()
    rec = dict(kode=kode, tanggal=str(df.index[-1].date()), close=bulat(c), chg=round(chg, 2),
               atr_pct=round(a / c * 100, 1), nilai20=round(float((df.close * df.volume).iloc[-20:].mean()) / 1e9, 1),
               vol_x=round(float(t.volume / t.vol20), 2) if t.vol20 else None,
               ma20=bulat(t.ma20), ma50=bulat(t.ma50), res60=bulat(res60))

    def hasil(status, alasan, pemicu="", **kw):
        return {**rec, "status": status, "alasan": alasan, "pemicu": pemicu, **kw}

    tren = c > t.ma50 and t.ma20 > t.ma50 and df.ma50.iloc[-1] > df.ma50.iloc[-11]
    if not tren:
        return hasil("no trade", "Bukan tren naik (harga/MA20 di bawah MA50 atau MA50 menurun)")
    piv, mode, e = zigzag(df)
    L = [p for p in piv if p[0] == "L"]; H = [p for p in piv if p[0] == "H"]
    if len(L) < 2 or len(H) < 2:
        return hasil("tunggu", "Swing belum cukup untuk membaca struktur", f"Tunggu struktur terbentuk; resistance {bulat(res60)}")
    hl = L[-1][2] > L[-2][2]
    hh = H[-1][2] >= H[-2][2] * 0.995
    if mode == "dn":
        hl = df.low.iloc[e] > L[-1][2]
    rec["hl"] = bulat(L[-1][2])
    if not (hl and hh):
        return hasil("tunggu", "Tren MA naik, tetapi struktur higher high/higher low belum rapi",
                     f"Tutup di atas {bulat(res60)} dengan volume, atau terbentuk higher low baru")
    if chg >= ara(y.close) - 2:
        return hasil("tunggu", "Candle mendekati ARA (momentum terpotong batas harga)", "Tunggu pullback/retest setelah ARA")

    setup = None
    if c > res60 and pos >= 0.7 and t.vol20 and t.volume >= 1.5 * t.vol20 and c - res60 <= a:
        stop = min(t.low, res60) - 0.25 * a
        target = res60 + (res60 - df.low.iloc[-30:-1].min())
        setup = ("Breakout", res60, stop, target, f"Tutup di atas resistance {bulat(res60)} dengan volume {t.volume / t.vol20:.1f}x")
    if setup is None:
        if mode == "up":
            leg_lo = L[-1][2]
            leg_hi = H[-1][2] if H[-1][1] > L[-1][1] else df.high.iloc[e]
        else:
            leg_lo, leg_hi = L[-1][2], H[-1][2]
        f382 = leg_hi - 0.382 * (leg_hi - leg_lo); f618 = leg_hi - 0.618 * (leg_hi - leg_lo)
        z_atas = max(f382, t.ma20 + 0.3 * a); z_bawah = min(f618, t.ma20 - 0.5 * a)
        low2 = min(t.low, y.low)
        sentuh = z_bawah - 0.5 * a <= low2 <= z_atas
        wick_bawah = (min(t.open, c) - t.low) / rng
        engulf = c > t.open and c > y.open and t.open <= y.close and y.close < y.open
        konfirm = pos >= 0.6 and c > t.open and (wick_bawah >= 0.3 or engulf or c > y.high) and c >= t.ma20
        if sentuh and konfirm:
            stop = min(t.low, y.low, z_bawah) - 0.25 * a
            target = max(leg_hi, df.high.iloc[-61:].max()) if leg_hi - c > 0.5 * a else df.high.max()
            setup = ("Pullback", z_bawah, stop, target,
                     f"Menyentuh zona {bulat(z_bawah)}–{bulat(z_atas)} lalu tutup kuat (posisi tutup {pos:.0%})")
        elif sentuh:
            return hasil("tunggu", "Harga di zona support, konfirmasi candle belum muncul",
                         f"Candle penolakan/tutup kuat di zona {bulat(z_bawah)}–{bulat(z_atas)}")
    if setup is None:
        dekat = (res60 - c) <= 0.5 * a or c > res60
        if dekat:
            return hasil("tunggu", "Tren & struktur naik, harga menempel/di atas resistance tanpa konfirmasi volume",
                         f"Tutup di atas {bulat(res60)} dengan volume ≥1,5x, atau retest {bulat(res60)} yang bertahan")
        return hasil("tunggu", "Tren & struktur naik, harga belum di level kunci",
                     f"Pullback ke sekitar MA20 {bulat(t.ma20)} lalu memantul, atau tembus {bulat(res60)}")
    jenis, level, stop, target, kenapa = setup
    risk, reward = c - stop, target - c
    rr = reward / risk if risk > 0 else 0
    kw = dict(setup=jenis, level=bulat(level), stop=bulat(stop), target=bulat(target),
              risk_pct=round(risk / c * 100, 1), rr=round(rr, 2))
    if rr >= 2:
        return hasil("tradeable", f"{jenis} terkonfirmasi. {kenapa}. Rasio 1:{rr:.1f}",
                     f"Invalid bila tutup di bawah {bulat(stop)}", **kw)
    return hasil("tunggu", f"{jenis} terbentuk tetapi rasio untung-rugi hanya 1:{rr:.1f} (< 1:2)",
                 f"Tunggu harga lebih dekat ke {bulat(level)} agar rasio ≥ 1:2", **kw)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dari-pickle")
    a = ap.parse_args()
    if a.dari_pickle:
        import pickle
        data = pickle.load(open(a.dari_pickle, "rb"))
    else:
        kode = semesta()
        if not kode:
            log("Semesta kosong; tidak ada yang dipindai."); sys.exit(1)
        log(f"Memindai {len(kode)} saham likuid...")
        data = unduh(kode)
    hasil = []
    for k, df in sorted(data.items()):
        try:
            hasil.append(nilai_saham(k, df))
        except Exception as e:  # noqa: BLE001
            log(f"{k}: {e}")
    if not hasil:
        log("Tidak ada hasil."); sys.exit(1)
    urut = {"tradeable": 0, "tunggu": 1, "no trade": 2}
    hasil.sort(key=lambda r: (urut[r["status"]], -(r.get("rr") or 0), -r["nilai20"]))
    jumlah = {s: sum(r["status"] == s for r in hasil) for s in urut}
    ma50_naik = sum(1 for k, d in data.items() if d.close.rolling(50).mean().iloc[-1] > d.close.rolling(50).mean().iloc[-11])
    chg3m = [d.close.iloc[-1] / d.close.iloc[-63] - 1 for d in data.values() if len(d) > 63]
    tgl = max(r["tanggal"] for r in hasil)
    out = {
        "diperbarui": dt.datetime.now(WIB).strftime("%d-%m-%Y %H:%M WIB"),
        "tanggal_data": tgl,
        "semesta": len(hasil),
        "jumlah": jumlah,
        "breadth": {"ma50_naik": ma50_naik, "median_3bln_pct": round(float(np.median(chg3m)) * 100, 1) if chg3m else None},
        "aturan": "Tren naik (harga>MA50, MA20>MA50, MA50 naik) + struktur HH/HL + level (pullback Fib 38,2–61,8%/MA20 "
                  "atau breakout resistance 60 hari dgn volume ≥1,5x) + konfirmasi candle + rasio ≥ 1:2. Hanya posisi beli.",
        "items": hasil,
    }
    DATA.mkdir(exist_ok=True)
    (DATA / "tradeable.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    log(f"OK tradeable.json: {jumlah} dari {len(hasil)} saham (data {tgl})")


if __name__ == "__main__":
    main()
