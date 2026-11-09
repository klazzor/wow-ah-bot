"""Ruleset ve AH ev listesini keşfet. Ham yanıtları data/raw/discover/ altına kaydeder.

Kullanım:
  python -m scripts.discover            # hafif: ruleset index/detay + ev listeleri
  python -m scripts.discover --houses   # ağır: her evi çekip ad/ilan sayısı/özet çıkar (~100 MB)
"""
import json
import sys
from pathlib import Path

from ahbot.api import get

OUT = Path("data/raw/discover")
OUT.mkdir(parents=True, exist_ok=True)


def save(name, data):
    (OUT / f"{name}.json").write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")


def light():
    st, idx, _ = get("/data/wow/ruleset/index")
    print(f"ruleset/index -> HTTP {st}")
    save("ruleset_index", idx)
    if st != 200:
        print(idx)
        return []
    rulesets = idx.get("rulesets", [])
    houses_by_rs = {}
    for rs in rulesets:
        rid = rs["id"]
        st, det, _ = get(f"/data/wow/ruleset/{rid}")
        save(f"ruleset_{rid}", det)
        name = (det or {}).get("name") if st == 200 else None
        print(f"\nRuleset {rid}: {rs.get('name') or name}  (detay HTTP {st})")
        if det:
            extra = {k: v for k, v in det.items() if k not in ("_links",)}
            print("  alanlar:", json.dumps(extra, ensure_ascii=False)[:400])
        st, ah, _ = get(f"/data/wow/ruleset/{rid}/auctions/")
        save(f"ruleset_{rid}_auctions_index", ah)
        print(f"  auctions/ -> HTTP {st}")
        if st == 200 and ah:
            lst = ah.get("auctions") or ah.get("auction_houses") or []
            houses_by_rs[rid] = [h.get("id") for h in lst]
            for h in lst:
                print(f"    ev {h.get('id'):>4}  {h.get('name')}")
    return houses_by_rs


def heavy(house_ids, rid):
    print(f"\n--- Evler (ruleset {rid} üzerinden) ---")
    for hid in house_ids:
        st, h, hdr = get(f"/data/wow/ruleset/{rid}/auctions/{hid}", timeout=300)
        if st != 200 or not h:
            print(f"ev {hid}: HTTP {st}")
            continue
        auc = h.get("auctions") or []
        com = h.get("commodities")
        keys = sorted(h.keys())
        sample = auc[0] if auc else None
        print(f"ev {hid:>4} {h.get('name')!s:30} ilan={len(auc):>6}  ruleset={h.get('ruleset')}  "
              f"last-modified={hdr.get('Last-Modified')}  anahtarlar={keys}")
        if sample and hid == house_ids[0]:
            print("   örnek ilan:", json.dumps(sample)[:300])
        summary = {k: v for k, v in h.items() if k != "auctions"}
        summary["auction_count"] = len(auc)
        save(f"house_{hid}_summary", summary)


if __name__ == "__main__":
    hb = light()
    if "--houses" in sys.argv and hb:
        rid = 1 if 1 in hb else next(iter(hb))
        ids = hb[rid]
        if "--ids" in sys.argv:
            ids = [int(x) for x in sys.argv[sys.argv.index("--ids") + 1].split(",")]
        heavy(ids, rid)
