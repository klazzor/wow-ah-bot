"""Disenchant fırsatları: AH'den yeşil/mavi item al → DE et → mat'ları AH'de sat.
Mat fiyatı = ev 113'ün güncel en ucuz commodity ilanı − 1c, − %5 AH payı, − beklenen depozito kaybı.
Kullanım: python -m scripts.disenchant_report → reports/disenchant_<tarih>.md
"""
from collections import defaultdict
from datetime import datetime

from ahbot import disenchant as de
from ahbot import tsm
from ahbot.analysis import p_unsold, velocity_flag
from ahbot.config import (AH_CUT_FACTION, CAPITAL_COPPER, DE_MIN_MARGIN, DE_MIN_PROFIT_COPPER,
                          DEFAULT_LIST_HOURS, DEPOSIT_FACTOR, ENCHANTING_SKILL, HOUSE_ID,
                          REPORTS_DIR, TRUSTED_SELL_SOURCES)
from ahbot.db import connect


def money(c):
    c = int(round(c))
    neg, c = c < 0, abs(c)
    g, s, b = c // 10000, (c // 100) % 100, c % 100
    return ("-" if neg else "") + (f"{g}g " if g else "") + (f"{s}s " if g or s else "") + f"{b}c"


def latest_snapshot(con, kind):
    return con.execute("SELECT id, last_modified FROM snapshots WHERE house_id=? AND kind=? ORDER BY id DESC LIMIT 1",
                       (HOUSE_ID, kind)).fetchone()


def mat_prices(con, sid):
    ids = list(de.MAT_NAMES)
    ph = ",".join("?" * len(ids))
    rows = con.execute(
        f"""SELECT s.item_id, s.min_unit, s.median_unit, s.qty, s.listing_count, i.name, i.sell_price
            FROM item_summary s LEFT JOIN items i USING(item_id)
            WHERE s.snapshot_id=? AND s.item_id IN ({ph})""", (sid, *ids)).fetchall()
    t = tsm.latest(con, set(ids))
    info, net = {}, {}
    for iid, mn, med, qty, n, name, sell in rows:
        dep = (sell or 0) * DEPOSIT_FACTOR[DEFAULT_LIST_HOURS]
        pu_src, pu = p_unsold(t.get(iid))   # TSM saleRate varsa onu kullan, yoksa varsayılan
        v = (mn - 1) * (1 - AH_CUT_FACTION) - pu * dep if mn else 0
        net[iid] = max(0, v)
        info[iid] = dict(name=name, min=mn, med=med, qty=qty, n=n, net=net[iid],
                         spd=(t.get(iid) or {}).get("g_spd"), pu=pu, pu_src=pu_src,
                         tsm_mv=(t.get(iid) or {}).get("r_mv"), vflag=velocity_flag(t.get(iid)))
    return info, net


def main():
    con = connect()
    sid_c, lm = latest_snapshot(con, "commodities")
    sid_i, _ = latest_snapshot(con, "items")
    info, net = mat_prices(con, sid_c)

    ph = ",".join("?" * len(TRUSTED_SELL_SOURCES))
    rows = con.execute(
        f"""SELECT a.item_id, i.name, i.quality, i.item_class, i.item_level, i.inventory_type,
                   a.unit_price, a.time_left
            FROM auction_seen a JOIN items i USING(item_id)
            WHERE a.snapshot_id=? AND a.unit_price IS NOT NULL
              AND i.item_class IN ('Weapon','Armor') AND i.quality IN ('UNCOMMON','RARE')
              AND i.item_level IS NOT NULL AND i.source IN ({ph})""",
        (sid_i, *TRUSTED_SELL_SOURCES)).fetchall()

    per_item = {}
    deals, skipped_skill = [], 0
    for iid, name, q, cls, ilvl, slot, price, tl in rows:
        if slot in de.NOT_DISENCHANTABLE_SLOTS or iid in de.NOT_DISENCHANTABLE_ITEMS:
            continue
        req = de.required_skill(ilvl)
        if req is None or req > ENCHANTING_SKILL:
            skipped_skill += 1
            continue
        if iid not in per_item:
            outs = de.outcomes(ilvl, q, cls)
            ev = de.expected_value(outs, net)
            maxbuy = min(ev - DE_MIN_PROFIT_COPPER, ev / (1 + DE_MIN_MARGIN))
            # EV'ye en çok katkı yapan mat → satış hızı uyarısı onun üzerinden
            main = max(outs, key=lambda o: o[1] * o[2] * net.get(o[0], 0))[0] if outs else None
            per_item[iid] = dict(name=name, q=q, cls=cls, ilvl=ilvl, req=req, ev=ev, maxbuy=maxbuy, n=0, cheap=None,
                                 main=main)
        p = per_item[iid]
        p["n"] += 1
        p["cheap"] = price if p["cheap"] is None else min(p["cheap"], price)
        if price <= p["maxbuy"]:
            mi = info.get(p["main"], {})
            warn = ["DE tablosu doğrulanmadı → önce 1 tane ile test et"]
            if mi.get("vflag"):
                warn.append(f"{mi.get('name')}: {mi['vflag']}")
            if price and (p["ev"] - price) / price > 1.0 and p["n"] >= 20:
                warn.append(f"kâr şüpheli derecede yüksek ve {p['n']} ilan var (Greater Magic Wand dersi: DE edilemiyor olabilir)")
            deals.append(dict(iid=iid, name=name, q=q, ilvl=ilvl, price=price, ev=p["ev"],
                              profit=p["ev"] - price, tl=tl, warn="; ".join(warn)))
    deals.sort(key=lambda d: -d["profit"] / d["price"])

    # Bütçe planı: aynı item'dan çok alım mat fiyatını düşürür → item başına en fazla 3
    budget, plan, cnt = CAPITAL_COPPER, [], defaultdict(int)
    for d in deals:
        if d["price"] <= budget and cnt[d["iid"]] < 3:
            plan.append(d)
            budget -= d["price"]
            cnt[d["iid"]] += 1

    # İsim kontrolü (tablo hafızadan girildi)
    mismatch = [(m, exp, info[m]["name"]) for m, exp in de.MAT_NAMES.items()
                if m in info and info[m]["name"] and info[m]["name"] != exp]

    REPORTS_DIR.mkdir(exist_ok=True)
    out = REPORTS_DIR / f"disenchant_{datetime.now():%Y%m%d_%H%M}.md"
    L = [f"# Disenchant fırsatları — ev {HOUSE_ID} (PvP Horde)", "",
         f"Snapshot: {lm} | Enchanting: {ENCHANTING_SKILL} | Bütçe: {money(CAPITAL_COPPER)} | "
         f"Eşik: kâr ≥ {money(DE_MIN_PROFIT_COPPER)} ve ≥ %{int(DE_MIN_MARGIN*100)}", "",
         "⚠️ DE oranları Classic Era tablosundan, Forever için doğrulanmadı. Sonuç rastgele; değerler ORTALAMA beklenti.",
         f"Beceri yetersizliğinden atlanan ilan: {skipped_skill}", ""]
    if mismatch:
        L += ["**Mat isim uyuşmazlığı (tabloyu kontrol et):** " +
              "; ".join(f"{m}: beklenen {e}, API {a}" for m, e, a in mismatch), ""]
    L += ["## Mat fiyatları (ev 113, şu an)", "",
          "Satış hızı = TSM bölge `soldPerDay` (tüm EU Forever realm'leri; bizim evin payı bilinmiyor). "
          "P(satılmama) = 1 − TSM `saleRate`; TSM verisi yoksa varsayılan.", "",
          "| Mat | ID | En ucuz | Medyan | TSM marketValue | Toplam adet | İlan | Bölge satış/gün | P(satılmama) | Net (pay+depozito sonrası) | Uyarı |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    for m, exp in de.MAT_NAMES.items():
        i = info.get(m)
        if i:
            spd = "—" if i["spd"] is None else f"{i['spd']:g}"
            L.append(f"| {exp} | {m} | {money(i['min'])} | {money(i['med'])} | "
                     f"{money(i['tsm_mv']) if i['tsm_mv'] else '—'} | {i['qty']} | {i['n']} | {spd} | "
                     f"{i['pu']:.2f} ({i['pu_src']}) | {money(i['net'])} | {i['vflag']} |")
        else:
            L.append(f"| {exp} | {m} | — | — | — | 0 | 0 | — | — | 0 (ilan yok → değer 0 sayıldı) | |")
    L += ["", "## Bütçeye göre önerilen alımlar", "",
          "| # | Item | ID | Kalite | ilvl | Alış | Beklenen DE değeri | Beklenen kâr | Kalan süre | Uyarı |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for n, d in enumerate(plan, 1):
        L.append(f"| {n} | {d['name']} | {d['iid']} | {d['q']} | {d['ilvl']} | {money(d['price'])} | "
                 f"{money(d['ev'])} | **{money(d['profit'])}** | {d['tl']} | {d['warn']} |")
    L.append(f"\nPlan: maliyet {money(CAPITAL_COPPER - budget)}, beklenen kâr {money(sum(d['profit'] for d in plan))} "
             f"({len(deals)} uygun ilan toplamda)\n")
    L += ["## Item başına en fazla alış fiyatı (TSM Sniper eşiği için, beklenen değere göre ilk 60)", "",
          "| Item | ID | Kalite | ilvl | Beklenen DE değeri | **En fazla alış** | AH'de en ucuz | İlan |",
          "|---|---|---|---|---|---|---|---|"]
    top = sorted((p | {"iid": k} for k, p in per_item.items() if p["maxbuy"] > 0), key=lambda p: -p["ev"])[:60]
    for p in top:
        L.append(f"| {p['name']} | {p['iid']} | {p['q']} | {p['ilvl']} | {money(p['ev'])} | {money(p['maxbuy'])} | "
                 f"{money(p['cheap'])} | {p['n']} |")
    out.write_text("\n".join(L), encoding="utf-8")
    print(f"{len(per_item)} DE edilebilir item, {len(deals)} uygun ilan, plan {len(plan)} kalem "
          f"(beklenen kâr {money(sum(d['profit'] for d in plan))}) → {out}")
    if mismatch:
        print("UYARI: mat isim uyuşmazlığı:", mismatch)


if __name__ == "__main__":
    main()
