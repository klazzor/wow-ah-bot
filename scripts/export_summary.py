"""Günlük fiyat özetleri: data/summary/YYYY-MM-DD.csv.gz (UTC gün).
Satırlar: fetched_ts (UTC), kind, item_id, min_unit, p10_unit, median_unit, qty, listing_count, bid_only_count — fiyatlar bakır.
gzip başlığında zaman/ad yok (mtime=0) → aynı içerik aynı bayt; git'te gereksiz değişiklik olmaz.

Kullanım: python -m scripts.export_summary [--all]   # varsayılan son 2 gün; --all = veritabanındaki tüm günler
"""
import csv
import gzip
import io
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from ahbot.config import DB_PATH, HOUSE_ID  # noqa: E402

OUT = ROOT / "data" / "summary"
COLS = ["fetched_ts", "kind", "item_id", "min_unit", "p10_unit", "median_unit", "qty", "listing_count", "bid_only_count"]


def main(days=2, all_days=False, db=None):
    con = sqlite3.connect(f"file:{(ROOT / (db or DB_PATH)).as_posix()}?mode=ro", uri=True)
    OUT.mkdir(parents=True, exist_ok=True)
    if all_days:
        day_list = sorted({r[0][:8] for r in con.execute("SELECT fetched_ts FROM snapshots WHERE house_id=?", (HOUSE_ID,))})
    else:
        today = datetime.now(timezone.utc).date()
        day_list = [(today - timedelta(days=i)).strftime("%Y%m%d") for i in range(days)]
    written = 0
    for d in day_list:
        rows = con.execute(
            "SELECT s.fetched_ts, s.kind, i.item_id, i.min_unit, i.p10_unit, i.median_unit, i.qty, i.listing_count, "
            "i.bid_only_count FROM item_summary i JOIN snapshots s ON s.id=i.snapshot_id "
            "WHERE s.house_id=? AND s.fetched_ts LIKE ? ORDER BY s.fetched_ts, s.kind, i.item_id",
            (HOUSE_ID, d + "T%")).fetchall()
        if not rows:
            continue
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        w.writerow(COLS)
        w.writerows(rows)
        raw = io.BytesIO()
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0, compresslevel=9) as gz:
            gz.write(buf.getvalue().encode("utf-8"))
        path = OUT / f"{d[:4]}-{d[4:6]}-{d[6:]}.csv.gz"
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(raw.getvalue())
        os.replace(tmp, path)
        written += 1
        print(f"özet: {path.name} ({len(rows)} satır, {path.stat().st_size / 1e3:.0f} KB)")
    con.close()
    return written


if __name__ == "__main__":
    main(all_days="--all" in sys.argv)
