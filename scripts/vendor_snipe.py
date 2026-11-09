"""Faz 0 vendor-snipe listesi: buyout birim fiyatı < NPC satış fiyatı olan ilanlar.
AH payı yok (NPC'ye satılıyor), risk yok. Bütçe filtresi: config.CAPITAL_COPPER.

Kullanım: python -m scripts.vendor_snipe  → reports/vendor_snipe_<tarih>.md
"""
from collections import defaultdict
from datetime import datetime

from ahbot.config import CAPITAL_COPPER, HOUSE_ID, REPORTS_DIR, TRUSTED_SELL_SOURCES
from ahbot.db import connect


def money(c):
    c = int(c)
    g, s, b = c // 10000, (c // 100) % 100, c % 100
    return (f"{g}g " if g else "") + (f"{s}s " if g or s else "") + f"{b}c"


Q = """
SELECT a.item_id, i.name, i.quality, a.unit_price, a.quantity, a.time_left, i.sell_price, s.kind, i.source
FROM auction_seen a
JOIN snapshots s ON s.id = a.snapshot_id
JOIN items i ON i.item_id = a.item_id
WHERE s.id IN (SELECT MAX(id) FROM snapshots WHERE house_id = ? GROUP BY kind)
  AND a.unit_price IS NOT NULL
  AND i.sell_price > 0
  AND a.unit_price < i.sell_price
  AND i.source IN ({})
""".format(",".join("?" * len(TRUSTED_SELL_SOURCES)))


def main():
    con = connect()
    rows = con.execute(Q, (HOUSE_ID, *TRUSTED_SELL_SOURCES)).fetchall()
    lm = con.execute("SELECT MAX(last_modified) FROM snapshots WHERE house_id=?", (HOUSE_ID,)).fetchone()[0]
    deals = []
    # Şüpheli fırsat kuralı: vendor fiyatı fallback kaynaktansa ya da aynı item'ın çok sayıda "ucuz" ilanı varsa
    # (satıcılar vendor fiyatını biliyor olmalı → fiyat verimiz yanlış olabilir; Slayer's Cape dersi)
    per_item = defaultdict(int)
    for r in rows:
        per_item[r[0]] += 1
    for iid, name, qual, unit, qty, tl, sell, kind, src in rows:
        cost = unit * qty
        profit = (sell - unit) * qty
        warn = []
        if src != "static-forever-eu":
            warn.append("vendor fiyatı fallback kaynaktan")
        if per_item[iid] >= 3:
            warn.append(f"aynı item'dan {per_item[iid]} ucuz ilan — vendor fiyatı yanlış olabilir")
        if warn:
            warn.append("→ önce 1 tane al, NPC fiyatını tooltip'ten kontrol et")
        deals.append(dict(iid=iid, name=name, qual=qual, unit=unit, qty=qty, tl=tl, sell=sell,
                          cost=cost, profit=profit, roi=profit / cost if cost else 0, kind=kind, warn="; ".join(warn)))
    # En iyi kâr/maliyet oranı önce (küçük sermayede önemli olan bu)
    deals.sort(key=lambda d: (-d["roi"], -d["profit"]))

    # Bütçe ile açgözlü seçim
    budget, plan = CAPITAL_COPPER, []
    for d in deals:
        if d["cost"] <= budget:
            plan.append(d)
            budget -= d["cost"]

    nometa = con.execute(
        "SELECT COUNT(*) FROM items WHERE source IS NULL OR source NOT IN ({})".format(
            ",".join("?" * len(TRUSTED_SELL_SOURCES))), TRUSTED_SELL_SOURCES).fetchone()[0]

    REPORTS_DIR.mkdir(exist_ok=True)
    out = REPORTS_DIR / f"vendor_snipe_{datetime.now():%Y%m%d_%H%M}.md"
    L = [f"# Vendor-snipe listesi — ev {HOUSE_ID} (PvP Horde)", "",
         f"Snapshot: {lm}  |  Bütçe: {money(CAPITAL_COPPER)}  |  Toplam fırsat: {len(deals)} ilan, "
         f"toplam potansiyel kâr {money(sum(d['profit'] for d in deals))}", "",
         "Kural: AH'den al → NPC'ye sat. AH payı/depozito yok. İlanlar snapshot'tan sonra satılmış olabilir.", "",
         f"Not: meta bulunamayan {nometa} item hesaba katılmadı.", "",
         "## Bütçeye göre önerilen alımlar (en iyi kâr/maliyet önce)", "",
         "| # | Item | ID | Adet | Birim alış | NPC birim | Toplam maliyet | Net kâr | Kalan süre | ⚠️ Uyarı |",
         "|---|---|---|---|---|---|---|---|---|---|"]
    for n, d in enumerate(plan, 1):
        L.append(f"| {n} | {d['name']} | {d['iid']} | {d['qty']} | {money(d['unit'])} | {money(d['sell'])} | "
                 f"{money(d['cost'])} | **{money(d['profit'])}** | {d['tl']} | {d['warn']} |")
    L.append(f"\nPlan toplamı: maliyet {money(CAPITAL_COPPER - budget)}, kâr "
             f"{money(sum(d['profit'] for d in plan))}\n")
    L += ["## Tüm fırsatlar (net kâra göre, ilk 100)", "",
          "| Item | ID | Adet | Birim alış | NPC birim | Maliyet | Net kâr | Kalan süre |",
          "|---|---|---|---|---|---|---|---|"]
    for d in sorted(deals, key=lambda d: -d["profit"])[:100]:
        L.append(f"| {d['name']} | {d['iid']} | {d['qty']} | {money(d['unit'])} | {money(d['sell'])} | "
                 f"{money(d['cost'])} | {money(d['profit'])} | {d['tl']} |")
    out.write_text("\n".join(L), encoding="utf-8")
    print(f"{len(deals)} fırsat, bütçe planı {len(plan)} kalem → {out}")


if __name__ == "__main__":
    main()
