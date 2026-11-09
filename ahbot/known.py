"""Kullanıcının bildiği tarifleri (data/user/known_recipes.csv) tarif tablosuyla eşleştirir.
- known_recipes tablosunu yeniden doldurur (bilinen tarif + renk + öğrenme kaynağı).
- recipes.skill_level boşsa ekran görüntüsündeki seviyeyi yazar (skill_source='user').
- API seviyesi ile ekran görüntüsü farklıysa çatışma olarak raporlar (üzerine yazmaz).
Kullanım: python -m ahbot.known
"""
import csv
from datetime import datetime, timezone
from pathlib import Path

from ahbot.db import connect

CSV = Path("data/user/known_recipes.csv")


def run():
    con = connect()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = list(csv.DictReader(CSV.open(encoding="utf-8")))
    con.execute("DELETE FROM known_recipes")
    unmatched, conflicts, filled, matched = [], [], 0, 0
    for r in rows:
        hits = con.execute("SELECT recipe_id, skill_level, skill_source FROM recipes "
                           "WHERE profession=? AND lower(name)=lower(?)", (r["profession"], r["name"])).fetchall()
        if not hits:
            unmatched.append(f"{r['profession']}: {r['name']}")
            continue
        lvl = int(r["learn_level"])
        for rid, api_lvl, src in hits:   # aynı adlı birden fazla tarif olabilir (ör. Faction Banner)
            matched += 1
            con.execute("INSERT OR REPLACE INTO known_recipes VALUES (?,?,?,?)", (rid, r["color"], r["source"], now))
            if api_lvl is None or src == "user":
                con.execute("UPDATE recipes SET skill_level=?, skill_source='user' WHERE recipe_id=?", (lvl, rid))
                filled += api_lvl is None
            elif api_lvl != lvl:
                conflicts.append(f"{r['name']} (recipe {rid}): API {api_lvl}, oyun {lvl}")
    con.commit()
    print(f"CSV satırı: {len(rows)} | eşleşen tarif kaydı: {matched} | seviyesi doldurulan: {filled}")
    print(f"Eşleşmeyen ({len(unmatched)}):", *unmatched, sep="\n  ")
    print(f"Seviye çatışması ({len(conflicts)}):", *conflicts, sep="\n  ")
    for prof in ("Tailoring", "Enchanting"):
        n = con.execute("SELECT COUNT(*), SUM(skill_level IS NOT NULL) FROM recipes WHERE profession=?", (prof,)).fetchone()
        print(f"{prof}: {n[0]} tarif, seviyesi bilinen {n[1]}")


if __name__ == "__main__":
    run()
