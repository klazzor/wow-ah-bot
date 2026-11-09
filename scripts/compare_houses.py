"""Aday Horde evlerini indirip (gzip ham JSON olarak saklar) yaygın item'ların
en düşük birim buyout fiyatlarını karşılaştırır. Amaç: oyundaki fiyatla eşleştirip
kullanıcının evini bulmak.

Kullanım: python -m scripts.compare_houses 113,114,120,124
"""
import gzip
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from ahbot.api import get

RULESET = 1
RAW = Path("data/raw/houses")
RAW.mkdir(parents=True, exist_ok=True)

ITEMS = {
    2589: "Linen Cloth", 2592: "Wool Cloth", 4306: "Silk Cloth", 4338: "Mageweave Cloth",
    14047: "Runecloth", 10940: "Strange Dust", 10938: "Lesser Magic Essence",
    2447: "Peacebloom", 2770: "Copper Ore", 2318: "Light Leather",
}


def money(c):
    if c is None:
        return "-"
    g, s, b = c // 10000, (c // 100) % 100, c % 100
    return (f"{g}g " if g else "") + (f"{s}s " if g or s else "") + f"{b}c"


def fetch(hid, commodities=False):
    path = f"/data/wow/ruleset/{RULESET}/auctions/{hid}" + ("/commodities" if commodities else "")
    st, h, hdr = get(path, timeout=300)
    if st != 200:
        raise RuntimeError(f"{path}: HTTP {st}")
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    tag = "_commodities" if commodities else ""
    with gzip.open(RAW / f"{hid}{tag}_{ts}.json.gz", "wt", encoding="utf-8") as f:
        json.dump({"fetched": ts, "last_modified": hdr.get("Last-Modified"), "data": h}, f)
    return h, hdr.get("Last-Modified")


def stats(auctions):
    per = defaultdict(list)  # item -> [(unit_price, qty)]
    for a in auctions:
        iid = a["item"]["id"]
        if iid not in ITEMS:
            continue
        if a.get("unit_price"):
            per[iid].append((a["unit_price"], a["quantity"]))
        elif a.get("buyout"):
            per[iid].append((a["buyout"] // a["quantity"], a["quantity"]))
    out = {}
    for iid, lst in per.items():
        lst.sort()
        out[iid] = {"min": lst[0][0], "min_qty": lst[0][1], "n": len(lst),
                    "cheap3": [money(p) + f" x{q}" for p, q in lst[:3]]}
    return out


if __name__ == "__main__":
    ids = [int(x) for x in sys.argv[1].split(",")]
    res = {}
    # Stack'lenebilir item'lar (kumaş, toz, ot, maden) ana ev yanıtında yok; /commodities'te.
    for hid in ids:
        h, lm = fetch(hid, commodities=True)
        res[hid] = stats(h.get("auctions", []))
        print(f"ev {hid} commodities: {len(h.get('auctions', []))} ilan, Last-Modified {lm}"
              f" | anahtarlar: {sorted(h.keys())}")
    print()
    for iid, name in ITEMS.items():
        print(f"{name} ({iid})")
        for hid in ids:
            s = res[hid].get(iid)
            if s:
                print(f"   ev {hid}: en ucuz 3 (birim) = {', '.join(s['cheap3'])}   | {s['n']} ilan")
            else:
                print(f"   ev {hid}: ilan yok")
