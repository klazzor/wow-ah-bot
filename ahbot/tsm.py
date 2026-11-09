"""TSM herkese açık CSV verisi (anahtar/limit yok): https://tradeskillmaster.com/public-data
- realm  : forever/eu/realm/pvp-horde/items.csv → itemId,name,marketValue,minBuyout,recent,historical,updatedAt
- region : forever/eu/region/items.csv          → itemId,name,marketValue,historical,avgSalePrice,saleRate,soldPerDay,updatedAt
Fiyatlar bakır. soldPerDay/saleRate BÖLGE GENELİ (tüm EU Forever realm'leri), sadece bizim ev değil.

Kullanım:
  python -m ahbot.tsm          # aralığı dolmuş olanları indir (realm 3 saat, region 24 saat)
  python -m ahbot.tsm --force  # ikisini de şimdi indir
"""
import csv
import io
import sys
from datetime import datetime, timedelta, timezone

import requests

from ahbot.db import connect

SOURCES = {
    "tsm_realm": ("https://public-data.tradeskillmaster.com/forever/eu/realm/pvp-horde/items.csv", timedelta(hours=3)),
    "tsm_region": ("https://public-data.tradeskillmaster.com/forever/eu/region/items.csv", timedelta(hours=24)),
}
COLS = {
    "tsm_realm": [("itemId", int), ("name", str), ("marketValue", int), ("minBuyout", int),
                  ("recent", int), ("historical", int), ("updatedAt", str)],
    "tsm_region": [("itemId", int), ("name", str), ("marketValue", int), ("historical", int),
                   ("avgSalePrice", int), ("saleRate", float), ("soldPerDay", float), ("updatedAt", str)],
}


def _conv(f, v):
    if v in ("", None):
        return None
    try:
        return f(v)
    except ValueError:
        return None


def _due(con, source, interval):
    r = con.execute("SELECT MAX(fetched_ts) FROM fetch_log WHERE source=? AND http_status=200", (source,)).fetchone()[0]
    return r is None or datetime.now(timezone.utc) - datetime.fromisoformat(r) >= interval


def fetch(con, source):
    url, _ = SOURCES[source]
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    r = requests.get(url, timeout=120)
    if r.status_code != 200:
        con.execute("INSERT INTO fetch_log VALUES (?,?,?,?,?)", (source, now, r.status_code, None, 0))
        con.commit()
        print(f"{source}: HTTP {r.status_code}")
        return
    rows = list(csv.DictReader(io.StringIO(r.content.decode("utf-8-sig"))))
    cols = COLS[source]
    missing = [c for c, _ in cols if rows and c not in rows[0]]
    if missing:
        raise RuntimeError(f"{source}: beklenen sütun yok: {missing} (TSM formatı değişmiş olabilir)")
    data = [tuple(_conv(f, row[c]) for c, f in cols) for row in rows]
    cur = con.executemany(f"INSERT OR IGNORE INTO {source} VALUES ({','.join('?' * len(cols))})", data)
    upd = rows[0]["updatedAt"] if rows else None
    con.execute("INSERT INTO fetch_log VALUES (?,?,?,?,?)", (source, now, 200, upd, len(rows)))
    con.commit()
    print(f"{source}: {len(rows)} satır, updatedAt {upd}, yeni eklenen {cur.rowcount}")


def run(force=False):
    con = connect()
    for source, (_, interval) in SOURCES.items():
        if force or _due(con, source, interval):
            fetch(con, source)
        else:
            print(f"{source}: aralık dolmadı, atlandı")


def latest(con, item_ids=None):
    """{item_id: {...realm alanları, ...region alanları}} — her tablonun en son updated_at'i."""
    out = {}
    for tbl, fields in (("tsm_realm", "market_value AS r_mv, min_buyout AS r_min, recent AS r_recent, historical AS r_hist"),
                        ("tsm_region", "market_value AS g_mv, avg_sale_price AS g_avg, sale_rate AS g_rate, sold_per_day AS g_spd")):
        ua = con.execute(f"SELECT MAX(updated_at) FROM {tbl}").fetchone()[0]
        if not ua:
            continue
        cur = con.execute(f"SELECT item_id, {fields}, updated_at FROM {tbl} WHERE updated_at=?", (ua,))
        names = [d[0] for d in cur.description]
        for row in cur:
            d = dict(zip(names, row))
            iid = d.pop("item_id")
            d[f"{tbl}_updated"] = d.pop("updated_at")
            if item_ids is None or iid in item_ids:
                out.setdefault(iid, {}).update(d)
    return out


if __name__ == "__main__":
    run(force="--force" in sys.argv)
