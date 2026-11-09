"""Tailoring + Enchanting tarif verisini API'den çeker (hafızadan HİÇBİR tarif yazılmaz).

Kaynaklar:
1. static-forever-eu: /profession/{id}/skill-tier/{tier} → /recipe/{id}  (classic1x'te bu uç noktalar 404)
2. Beceri seviyesi: tarif uç noktasında yok → tarifi öğreten Pattern/Formula item'ının
   preview_item.requirements.skill.level alanından. Bu item'lar item arama uç noktasıyla bulunur
   (önce forever, item 404 ise classic1x). Eşleşme: öğretilen ürün item ID'si ya da tarif adı.
   Trainer'dan öğrenilen tariflerin seviyesi API'de yok → kullanıcıya listelenir.
3. Forever'a özel işaret: ürün Classic Era'da (classic1x) yoksa veya kategori "SEASON OF DISCOVERY" ise.

Kullanım: python -m ahbot.recipes
"""
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from ahbot.api import NS_STATIC, get
from ahbot.db import connect

PROFS = {"Tailoring": (197, 2948), "Enchanting": (333, 2940)}
NS_CLASSIC = "static-classic1x-eu"
TEACH_PREFIX = re.compile(r"^(Pattern|Formula|Recipe|Plans|Schematic|Manual):\s*", re.I)


def _recipe_list():
    out = []
    for prof, (pid, tier) in PROFS.items():
        st, d, _ = get(f"/data/wow/profession/{pid}/skill-tier/{tier}", namespace=NS_STATIC)
        if st != 200:
            raise RuntimeError(f"{prof} skill-tier: HTTP {st}")
        for c in d.get("categories", []):
            for r in c.get("recipes", []):
                out.append((r["id"], prof, c.get("name")))
    return out


def _recipe_detail(args):
    rid, prof, cat = args
    st, d, _ = get(f"/data/wow/recipe/{rid}", namespace=NS_STATIC, timeout=30)
    return rid, prof, cat, st, d


def _search_teach_items():
    """Recipe sınıfı (item_class 9) item'larını arama uç noktasıyla bul. Çalışmazsa boş liste."""
    found, page = {}, 1
    while True:
        st, d, _ = get("/data/wow/search/item", namespace=NS_STATIC, timeout=60,
                       **{"item_class.id": 9, "_pageSize": 1000, "_page": page, "orderby": "id"})
        if st != 200 or not d:
            print(f"  item arama: HTTP {st} (sayfa {page})")
            break
        for r in d.get("results", []):
            data = r.get("data", {})
            name = (data.get("name") or {}).get("en_US") or ""
            if TEACH_PREFIX.match(name):
                found[data["id"]] = name
        if page >= d.get("pageCount", 1):
            break
        page += 1
    return found


def _teach_detail(iid):
    for ns in (NS_STATIC, NS_CLASSIC):
        st, d, _ = get(f"/data/wow/item/{iid}", namespace=ns, timeout=30)
        if st == 200 and d:
            pv = d.get("preview_item") or {}
            req = (pv.get("requirements") or {}).get("skill") or {}
            rec = pv.get("recipe") or {}
            crafted = ((rec.get("item") or {}).get("item") or {}).get("id")
            return dict(iid=iid, name=d.get("name"), ns=ns,
                        prof=(req.get("profession") or {}).get("name"), level=req.get("level"),
                        crafted=crafted)
    return None


def _in_classic(iid):
    st, _, _ = get(f"/data/wow/item/{iid}", namespace=NS_CLASSIC, timeout=30)
    return iid, (1 if st == 200 else 0 if st == 404 else None)


def run(workers=8):
    con = connect()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    lst = _recipe_list()
    print(f"Tarif listesi: {len(lst)} ({', '.join(f'{p}: {sum(1 for x in lst if x[1] == p)}' for p in PROFS)})")
    with ThreadPoolExecutor(workers) as ex:
        details = list(ex.map(_recipe_detail, lst))
    failed = [(rid, st) for rid, _, _, st, _ in details if st != 200]

    rows, reag = [], []
    for rid, prof, cat, st, d in details:
        if st != 200:
            continue
        cq = d.get("crafted_quantity") or {}
        qmin = cq.get("value", cq.get("minimum"))
        qmax = cq.get("value", cq.get("maximum"))
        rows.append(dict(recipe_id=rid, profession=prof, category=cat, name=d.get("name"),
                         crafted_item_id=(d.get("crafted_item") or {}).get("id"),
                         crafted_qty_min=qmin, crafted_qty_max=qmax, description=d.get("description")))
        for r in d.get("reagents", []):
            reag.append((rid, r["reagent"]["id"], r["reagent"].get("name"), r["quantity"]))

    # Ürün Classic Era'da var mı?
    crafted = sorted({r["crafted_item_id"] for r in rows if r["crafted_item_id"]})
    print(f"Ürün item'ı olan tarif: {sum(1 for r in rows if r['crafted_item_id'])}; Classic Era kontrolü: {len(crafted)} item")
    with ThreadPoolExecutor(workers) as ex:
        in_classic = dict(ex.map(_in_classic, crafted))

    # Öğreten item'lar → beceri seviyesi
    print("Pattern/Formula item'ları aranıyor...")
    teach_ids = _search_teach_items()
    print(f"  arama sonucu öğretici item: {len(teach_ids)}")
    with ThreadPoolExecutor(workers) as ex:
        teach = [t for t in ex.map(_teach_detail, list(teach_ids)) if t and t["prof"] in PROFS]
    by_crafted = {t["crafted"]: t for t in teach if t["crafted"]}
    by_name = {TEACH_PREFIX.sub("", t["name"] or "").strip().lower(): t for t in teach}

    for r in rows:
        t = by_crafted.get(r["crafted_item_id"]) if r["crafted_item_id"] else None
        t = t or by_name.get((r["name"] or "").strip().lower())
        r["skill_level"] = t["level"] if t else None
        r["skill_source"] = f"pattern:{t['iid']}@{t['ns']}" if t else None
        r["teach_item_id"] = t["iid"] if t else None
        ic = in_classic.get(r["crafted_item_id"]) if r["crafted_item_id"] else None
        r["crafted_in_classic1x"] = ic
        flags = []
        if ic == 0:
            flags.append("ürün Classic Era'da yok")
        if (r["category"] or "").upper() == "SEASON OF DISCOVERY":
            flags.append("SoD kategorisi")
        r["forever_flag"] = "; ".join(flags) or None
        r["source"] = NS_STATIC
        r["fetched_ts"] = now

    cols = ["recipe_id", "profession", "category", "name", "crafted_item_id", "crafted_qty_min", "crafted_qty_max",
            "description", "source", "skill_level", "skill_source", "teach_item_id", "crafted_in_classic1x",
            "forever_flag", "fetched_ts"]
    # Kullanıcının elle girdiği seviyeler korunur
    user_levels = dict(con.execute("SELECT recipe_id, skill_level FROM recipes WHERE skill_source='user'"))
    for r in rows:
        if r["recipe_id"] in user_levels and r["skill_level"] is None:
            r["skill_level"], r["skill_source"] = user_levels[r["recipe_id"]], "user"
    con.execute("DELETE FROM recipe_reagents")
    con.executemany(f"INSERT OR REPLACE INTO recipes ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                    [tuple(r[c] for c in cols) for r in rows])
    con.executemany("INSERT OR REPLACE INTO recipe_reagents VALUES (?,?,?,?)", reag)
    con.commit()

    print(f"\nKaydedilen tarif: {len(rows)} | detayı alınamayan: {len(failed)} {failed[:10]}")
    for prof in PROFS:
        n = con.execute("SELECT COUNT(*), SUM(skill_level IS NOT NULL), SUM(forever_flag IS NOT NULL) "
                        "FROM recipes WHERE profession=?", (prof,)).fetchone()
        print(f"  {prof}: {n[0]} tarif | beceri seviyesi bilinen: {n[1]} | Forever'a özel işaretli: {n[2]}")


if __name__ == "__main__":
    run()
