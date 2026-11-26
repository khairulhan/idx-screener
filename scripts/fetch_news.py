"""
Pengambil berita untuk IDX Screener.

1. Membaca daftar RSS di feeds.txt (bawaan: Google News Indonesia untuk kata kunci saham).
2. Menandai emiten yang disebut (kode 4 huruf atau nama perusahaan dari data/latest.csv).
3. Memberi sentimen -2..+2 per emiten:
     - jika secret ANTHROPIC_API_KEY ada  -> dinilai Claude (lebih akurat, berbayar per pemakaian)
     - jika tidak                         -> kamus kata kunci sederhana (gratis, kasar)

Hasil: data/news.json
"""
import calendar
import csv
import datetime as dt
import json
import os
import pathlib
import re
import sys
import time
import urllib.request
import zoneinfo

WIB = zoneinfo.ZoneInfo("Asia/Jakarta")
ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
JAM_KE_BELAKANG = 36

POSITIF = ["naik", "menguat", "melonjak", "melesat", "terbang", "rekor", "laba bersih tumbuh", "laba naik",
           "dividen", "buyback", "akuisisi", "kontrak baru", "ekspansi", "diborong", "akumulasi", "net buy",
           "upgrade", "ara", "positif", "tumbuh", "rebound", "teken kontrak", "right issue terserap"]
NEGATIF = ["turun", "melemah", "anjlok", "ambles", "ambrol", "rugi", "merosot", "suspensi", "suspend",
           "gagal bayar", "pkpu", "pailit", "digugat", "arb", "net sell", "dilepas", "downgrade",
           "delisting", "koreksi", "tertekan", "negatif", "unusual market activity", "uma"]
BUKAN_KODE = {"IHSG", "BUMN", "OJK", "KSEI", "BBM", "PPKM", "USD", "IDR", "CEO", "IPO", "RUPS", "MSCI",
              "LQ45", "IDX", "BEI", "BUMD", "APBN", "PDB", "BI", "THE", "AND", "FOR"}


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def muat_emiten():
    """Kode dan nama emiten dari data/latest.csv (dibuat oleh fetch_idx.py)."""
    kode, nama = set(), {}
    f = DATA / "latest.csv"
    if f.exists():
        with open(f, encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                k = r["Kode"].strip().upper()
                kode.add(k)
                n = re.sub(r"\b(tbk|pt|persero)\b\.?", "", (r.get("Nama") or ""), flags=re.I).strip(" .,")
                if len(n) >= 8:
                    nama[n.lower()] = k
    return kode, nama


def muat_feeds():
    f = ROOT / "feeds.txt"
    return [x.strip() for x in f.read_text(encoding="utf-8").splitlines()
            if x.strip() and not x.strip().startswith("#")]


def ambil_berita():
    import feedparser
    batas = time.time() - JAM_KE_BELAKANG * 3600
    items, lihat = [], set()
    for url in muat_feeds():
        try:
            fd = feedparser.parse(url, agent="Mozilla/5.0 idx-screener")
        except Exception as e:  # noqa: BLE001
            log("RSS gagal:", url, e)
            continue
        for e in fd.entries:
            judul = re.sub(r"\s+", " ", e.get("title", "")).strip()
            if not judul:
                continue
            kunci = judul.lower()[:90]
            if kunci in lihat:
                continue
            ts = e.get("published_parsed") or e.get("updated_parsed")
            detik = calendar.timegm(ts) if ts else time.time()
            if detik < batas:
                continue
            lihat.add(kunci)
            src = e.get("source", {}).get("title") if isinstance(e.get("source"), dict) else None
            items.append({
                "judul": judul,
                "link": e.get("link", ""),
                "sumber": src or fd.feed.get("title", ""),
                "detik": detik,
                "waktu": dt.datetime.fromtimestamp(detik, WIB).strftime("%d-%m %H:%M"),
            })
    items.sort(key=lambda x: -x["detik"])
    return items


def tandai_emiten(items, kode, nama):
    for it in items:
        teks = it["judul"]
        ditemukan = {k for k in re.findall(r"\b[A-Z]{4}\b", teks) if k in kode and k not in BUKAN_KODE}
        low = teks.lower()
        for n, k in nama.items():
            if n in low:
                ditemukan.add(k)
        it["kode"] = sorted(ditemukan)
    return items


def sentimen_kamus(items):
    hasil = {}
    for it in items:
        if not it["kode"]:
            continue
        low = it["judul"].lower()
        ada = lambda w: re.search(r"\b" + re.escape(w) + r"\b", low) is not None  # noqa: E731
        skor = sum(ada(w) for w in POSITIF) - sum(ada(w) for w in NEGATIF)
        for k in it["kode"]:
            h = hasil.setdefault(k, {"total": 0, "n": 0, "contoh": it["judul"]})
            h["total"] += max(-2, min(2, skor))
            h["n"] += 1
    return {k: {"s": max(-2, min(2, round(v["total"] / v["n"] * 1.5))), "n": v["n"],
                "katalis": v["contoh"][:90], "horizon": "", "metode": "kamus"}
            for k, v in hasil.items()}


def sentimen_claude(items, key):
    model = os.environ.get("CLAUDE_MODEL", "claude-haiku-5-5")
    daftar = "\n".join(f"- {it['judul']}" + (f" [emiten terdeteksi: {', '.join(it['kode'])}]" if it["kode"] else "")
                       for it in items[:120])
    prompt = (
        "Anda analis pasar saham Indonesia (BEI). Berikut judul berita terbaru. Identifikasi emiten BEI "
        "yang disebut (kode 4 huruf, atau nama perusahaan yang Anda yakin kodenya). Jangan menambah emiten "
        "yang tidak disebut dan jangan mengarang fakta di luar judul.\n"
        "Balas HANYA JSON array: [{\"kode\":\"XXXX\",\"sentimen\":-2..2 (bilangan bulat, dampak harga jangka pendek),"
        "\"sebutan\":jumlah judul,\"katalis\":\"maks 10 kata\",\"horizon\":\"intraday|beberapa hari|jangka menengah\"}]\n\n"
        + daftar)
    body = json.dumps({"model": model, "max_tokens": 4000,
                       "messages": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=body, headers={
        "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        js = json.load(r)
    teks = "".join(b.get("text", "") for b in js.get("content", []) if b.get("type") == "text")
    m = re.search(r"\[[\s\S]*\]", teks)
    arr = json.loads(m.group(0)) if m else []
    out = {}
    for x in arr:
        k = re.sub(r"[^A-Z]", "", str(x.get("kode", "")).upper())
        if not 3 <= len(k) <= 5:
            continue
        out[k] = {"s": max(-2, min(2, int(round(float(x.get("sentimen", 0)))))),
                  "n": max(1, int(x.get("sebutan", 1) or 1)),
                  "katalis": str(x.get("katalis", ""))[:90],
                  "horizon": str(x.get("horizon", ""))[:20], "metode": "claude"}
    return out


def main():
    kode, nama = muat_emiten()
    items = tandai_emiten(ambil_berita(), kode, nama)
    log(f"{len(items)} judul, {sum(1 for i in items if i['kode'])} menyebut emiten")
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    sent, metode = {}, "kamus kata kunci"
    if key and items:
        try:
            sent, metode = sentimen_claude(items, key), "Claude"
        except Exception as e:  # noqa: BLE001
            log("Claude gagal, pakai kamus:", e)
    if not sent:
        sent = sentimen_kamus(items)
        metode = "kamus kata kunci"
    for it in items:
        it.pop("detik", None)
    out = {
        "diperbarui": dt.datetime.now(WIB).strftime("%d-%m-%Y %H:%M WIB"),
        "metode_sentimen": metode,
        "items": items[:150],
        "sentimen": sent,
    }
    DATA.mkdir(exist_ok=True)
    (DATA / "news.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"OK news.json: {len(sent)} emiten diberi sentimen ({metode})")


if __name__ == "__main__":
    main()
