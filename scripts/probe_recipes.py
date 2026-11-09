"""Tarif verisi için hangi uç noktaların çalıştığını yoklar (hafif, ~15 istek).
Ham yanıtlar data/raw/probe/ altına kaydedilir.

Denenenler (her iki namespace'te: static-forever-eu, static-classic1x-eu):
  /data/wow/profession/index
  /data/wow/profession/{tailoring=197, enchanting=333}
  /data/wow/recipe/{id}           (profession'dan bulunan ilk tarif)
  /data/wow/item/5771             (bir Pattern item'ı — preview_item.recipe alanı var mı?)
Kullanım: python -m scripts.probe_recipes
"""
import json
from pathlib import Path

from ahbot.api import get

OUT = Path("data/raw/probe")
OUT.mkdir(parents=True, exist_ok=True)
NAMESPACES = ["static-forever-eu", "static-classic1x-eu"]
PROFS = {"Tailoring": 197, "Enchanting": 333}
PATTERN_ITEM = 5771   # bir Tailoring pattern item'ı; adı API'den okunup yazdırılır


def show(tag, st, d):
    keys = sorted(d.keys()) if isinstance(d, dict) else None
    print(f"  {tag}: HTTP {st}  anahtarlar={keys}")


def save(name, d):
    (OUT / f"{name}.json").write_text(json.dumps(d, indent=1, ensure_ascii=False), encoding="utf-8")


for ns in NAMESPACES:
    print(f"\n=== {ns} ===")
    st, d, _ = get("/data/wow/profession/index", namespace=ns)
    show("profession/index", st, d)
    save(f"{ns}_profession_index", d)
    if st == 200 and d:
        names = [(p.get("id"), p.get("name")) for p in d.get("professions", [])]
        print("   meslekler:", names)
    for pname, pid in PROFS.items():
        st, d, _ = get(f"/data/wow/profession/{pid}", namespace=ns)
        show(f"profession/{pid} ({pname})", st, d)
        save(f"{ns}_profession_{pid}", d)
        if st == 200 and d:
            tiers = d.get("skill_tiers") or []
            print("   skill_tiers:", [(t.get("id"), t.get("name")) for t in tiers][:10])
            for t in tiers[:1]:
                st2, d2, _ = get(f"/data/wow/profession/{pid}/skill-tier/{t['id']}", namespace=ns)
                show(f"skill-tier/{t['id']}", st2, d2)
                save(f"{ns}_skilltier_{pid}_{t['id']}", d2)
                if st2 == 200 and d2:
                    cats = d2.get("categories") or []
                    rec = next((r for c in cats for r in c.get("recipes", [])), None)
                    print(f"   kategori: {len(cats)}, toplam tarif: {sum(len(c.get('recipes', [])) for c in cats)}")
                    if rec:
                        st3, d3, _ = get(f"/data/wow/recipe/{rec['id']}", namespace=ns)
                        show(f"recipe/{rec['id']}", st3, d3)
                        save(f"{ns}_recipe_{rec['id']}", d3)
    st, d, _ = get(f"/data/wow/item/{PATTERN_ITEM}", namespace=ns)
    show(f"item/{PATTERN_ITEM}", st, d)
    save(f"{ns}_item_{PATTERN_ITEM}", d)
    if st == 200 and d:
        pv = d.get("preview_item") or {}
        print(f"   ad: {d.get('name')} | sınıf: {(d.get('item_class') or {}).get('name')} | "
              f"preview_item anahtarları: {sorted(pv.keys())}")
        if "recipe" in pv:
            print("   preview_item.recipe:", json.dumps(pv["recipe"], ensure_ascii=False)[:500])
