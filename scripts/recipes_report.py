"""Tarif verisi özeti + beceri seviyesi eksik tarifler listesi (sadece yerel DB'yi okur).
Kullanım: python -m scripts.recipes_report → reports/recipes_<tarih>.md
"""
from collections import Counter, defaultdict
from datetime import datetime

from ahbot.config import REPORTS_DIR
from ahbot.db import connect


def main():
    con = connect()
    reag = defaultdict(list)
    for rid, name, q in con.execute("SELECT recipe_id, name, quantity FROM recipe_reagents ORDER BY recipe_id"):
        reag[rid].append(f"{q}× {name}")
    rows = con.execute(
        """SELECT r.recipe_id, r.profession, r.category, r.name, r.crafted_item_id, r.skill_level, r.skill_source,
                  r.forever_flag, i.item_level, i.required_level
           FROM recipes r LEFT JOIN items i ON i.item_id = r.crafted_item_id
           ORDER BY r.profession, r.skill_level, r.category, r.name""").fetchall()

    L = [f"# Tarif verisi — {datetime.now():%Y-%m-%d %H:%M}", "",
         "Kaynak: `static-forever-eu` profession/recipe uç noktaları. Beceri seviyesi: tarifi öğreten "
         "Pattern/Formula item'ından (`requirements.skill.level`). Renk eşikleri API'de yok.", ""]
    for prof in ("Tailoring", "Enchanting"):
        pr = [r for r in rows if r[1] == prof]
        known = [r for r in pr if r[5] is not None]
        L += [f"## {prof}: {len(pr)} tarif", "",
              f"- Beceri seviyesi bilinen: {len(known)} (hepsi pattern/formula'dan)",
              f"- Bilinmeyen: {len(pr) - len(known)} (çoğu trainer tarifi olmalı)",
              f"- Forever'a özel işaretli: {sum(1 for r in pr if r[7])}", "",
              "| Kategori | Tarif | Seviye bilinen | Forever'a özel |", "|---|---|---|---|"]
        cats = Counter(r[2] for r in pr)
        for c, n in cats.most_common():
            L.append(f"| {c} | {n} | {sum(1 for r in pr if r[2] == c and r[5] is not None)} | "
                     f"{sum(1 for r in pr if r[2] == c and r[7])} |")
        L += ["", f"### {prof}: beceri seviyesi BİLİNMEYEN tarifler", "",
              "Oyunda trainer penceresinden seviyesine bakılabilir. `ilvl/req` = ürün item'ının seviyesi (ipucu, varsa).", "",
              "| Recipe ID | Kategori | Tarif | Malzemeler | ilvl/req | Forever işareti |", "|---|---|---|---|---|---|"]
        for r in pr:
            if r[5] is None:
                lv = f"{r[8]}/{r[9]}" if r[8] is not None else ""
                L.append(f"| {r[0]} | {r[2]} | {r[3]} | {', '.join(reag[r[0]])} | {lv} | {r[7] or ''} |")
        L += ["", f"### {prof}: seviyesi bilinen tarifler", "",
              "| Seviye | Tarif | Kategori | Malzemeler | Kaynak | Forever işareti |", "|---|---|---|---|---|---|"]
        for r in known:
            L.append(f"| {r[5]} | {r[3]} | {r[2]} | {', '.join(reag[r[0]])} | {r[6]} | {r[7] or ''} |")
        L.append("")
    REPORTS_DIR.mkdir(exist_ok=True)
    out = REPORTS_DIR / f"recipes_{datetime.now():%Y%m%d_%H%M}.md"
    out.write_text("\n".join(L), encoding="utf-8")
    print(f"→ {out}")
    for prof in ("Tailoring", "Enchanting"):
        print(prof, "seviye dağılımı (bilinen):",
              sorted(Counter((r[5] // 25) * 25 for r in rows if r[1] == prof and r[5] is not None).items()))


if __name__ == "__main__":
    main()
