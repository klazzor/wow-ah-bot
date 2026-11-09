"""Ev 113'ün item + commodity snapshot'ını çeker, ham gzip JSON'u saklar, SQLite'a özet yazar.

Kullanım:
  python -m ahbot.collector             # API'den taze çek
  python -m ahbot.collector --from-raw  # data/raw/houses içindeki en son dosyaları kullan
"""
import gzip
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone

from ahbot.api import get
from ahbot.config import HOUSE_ID, RAW_DIR, RULESET_ID
from ahbot.db import connect


def _fetch(kind):
    path = f"/data/wow/ruleset/{RULESET_ID}/auctions/{HOUSE_ID}"
    if kind == "commodities":
        path += "/commodities"
    st, data, hdr = get(path, timeout=300)
    if st != 200:
        raise RuntimeError(f"{path}: HTTP {st}")
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    tag = "_commodities" if kind == "commodities" else ""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    raw = RAW_DIR / f"{HOUSE_ID}{tag}_{ts}.json.gz"
    with gzip.open(raw, "wt", encoding="utf-8") as f:
        json.dump({"fetched": ts, "last_modified": hdr.get("Last-Modified"), "data": data}, f)
    return {"fetched": ts, "last_modified": hdr.get("Last-Modified"), "data": data}, raw


def _latest_raw(kind):
    pat = f"{HOUSE_ID}_commodities_*.json.gz" if kind == "commodities" else f"{HOUSE_ID}_2*.json.gz"
    files = sorted(RAW_DIR.glob(pat))
    if not files:
        raise FileNotFoundError(f"{RAW_DIR}/{pat} yok")
    with gzip.open(files[-1], "rt", encoding="utf-8") as f:
        return json.load(f), files[-1]


def unit_price(a):
    if a.get("unit_price"):
        return a["unit_price"]
    if a.get("buyout"):
        return a["buyout"] // a["quantity"]
    return None


def _wpct(pairs, q):
    """pairs: sıralı [(fiyat, adet)], adet ağırlıklı yüzdelik."""
    total = sum(n for _, n in pairs)
    target = q * total
    acc = 0
    for p, n in pairs:
        acc += n
        if acc >= target:
            return p
    return pairs[-1][0]


def store(con, kind, payload, raw_path):
    auctions = payload["data"].get("auctions", [])
    cur = con.execute(
        "INSERT OR IGNORE INTO snapshots (house_id, kind, last_modified, fetched_ts, n_auctions, raw_path)"
        " VALUES (?,?,?,?,?,?)",
        (HOUSE_ID, kind, payload["last_modified"], payload["fetched"], len(auctions), str(raw_path)),
    )
    if cur.rowcount == 0:
        print(f"{kind}: bu snapshot ({payload['last_modified']}) zaten kayıtlı, atlandı")
        return None
    sid = cur.lastrowid
    per = defaultdict(list)
    bid_only = defaultdict(int)
    rows = []
    for a in auctions:
        iid = a["item"]["id"]
        up = unit_price(a)
        rows.append((sid, a["id"], iid, up, a["quantity"], a.get("time_left")))
        if up is None:
            bid_only[iid] += 1
        else:
            per[iid].append((up, a["quantity"]))
    con.executemany("INSERT INTO auction_seen VALUES (?,?,?,?,?,?)", rows)
    summ = []
    for iid in set(per) | set(bid_only):
        pairs = sorted(per.get(iid, []))
        if pairs:
            summ.append((sid, iid, pairs[0][0], _wpct(pairs, 0.10), _wpct(pairs, 0.5),
                         sum(n for _, n in pairs), len(pairs), bid_only[iid]))
        else:
            summ.append((sid, iid, None, None, None, 0, 0, bid_only[iid]))
    con.executemany("INSERT INTO item_summary VALUES (?,?,?,?,?,?,?,?)", summ)
    con.commit()
    print(f"{kind}: snapshot #{sid}, {len(auctions)} ilan, {len(summ)} farklı item, Last-Modified {payload['last_modified']}")
    return sid


def prune_raw(keep_days):
    """keep_days günden eski ham dosyaları sil (SQLite özetleri kalır)."""
    import time
    cutoff = time.time() - keep_days * 86400
    n = 0
    for f in RAW_DIR.glob("*.json.gz"):
        if f.stat().st_mtime < cutoff:
            f.unlink()
            n += 1
    if n:
        print(f"{n} eski ham dosya silindi (> {keep_days} gün)")


def run(from_raw=False):
    con = connect()
    ids = {}
    for kind in ("items", "commodities"):
        payload, raw = _latest_raw(kind) if from_raw else _fetch(kind)
        ids[kind] = store(con, kind, payload, raw)
        if ids[kind] is None and not from_raw:
            raw.unlink(missing_ok=True)   # aynı snapshot: tekrar eden ham dosyayı tutma
    return ids


if __name__ == "__main__":
    run(from_raw="--from-raw" in sys.argv)
