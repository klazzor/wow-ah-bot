"""'Kendi malım' raporu: envanterdeki her item için seçenekleri net değerle karşılaştırır.
Envanter: TSM SavedVariables (çanta+banka+posta; AH'dekiler hariç). Fiyat: ev 113 son snapshot,
satış hızı: TSM bölge soldPerDay/saleRate. Değerleme ve şüpheli fırsat kuralı: ahbot/market.py.

Seçenekler (birim başına net değer):
- NPC'ye sat / AH'ye koy / (yeşil-mavi ise) DE et + mat sat
- Bildiğin bir tarifte kullan → ürünü sat (AH/NPC/DE hangisi iyiyse). Diğer malzemeler satın alınır
  (vendor kanıtlıysa vendor, yoksa AH en ucuz). Bolt gibi ara ürünler kumaş cinsinden sayılır.

Kullanım: python -m scripts.own_goods → reports/own_goods_<tarih>.md
"""
from collections import defaultdict
from datetime import datetime

from ahbot import disenchant as de
from ahbot import items as itemsmod
from ahbot import savedvars
from ahbot.config import HOUSE_ID, REPORTS_DIR
from ahbot.db import connect
from ahbot.market import Market, money


def main():
    inv, sv = savedvars.inventory()
    con = connect()

    known = con.execute("""SELECT r.recipe_id, r.name, r.crafted_item_id, r.crafted_qty_min, k.color
                           FROM known_recipes k JOIN recipes r USING(recipe_id)""").fetchall()
    reag = defaultdict(list)
    for rid, iid, q in con.execute("SELECT recipe_id, item_id, quantity FROM recipe_reagents"):
        reag[rid].append((iid, q))
    known_ids = {r[0] for r in known}
    need = (set(inv) | {p for _, _, p, _, _ in known if p} | set(de.MAT_NAMES)
            | {i for rid, v in reag.items() if rid in known_ids for i, _ in v})
    itemsmod.ensure(need)            # eksik meta (ağ isteği)
    mk = Market(con)

    # Ara ürün dönüşümleri: tek malzemeli, bildiğin tarif (ör. 2 Linen → 1 Bolt)
    conv = {}
    for rid, name, pid, qty, _ in known:
        if pid and len(reag[rid]) == 1 and (qty or 1) == 1:
            src, k = reag[rid][0]
            conv[pid] = (src, k)

    rows = []
    for iid, have in sorted(inv.items(), key=lambda x: mk.name(x[0])):
        m = mk.meta.get(iid) or {}
        opts = []  # (ad, birim net, not, uyarılar)
        vs, vnote = mk.vendor_sell(iid)
        if vs:
            opts.append(("NPC'ye sat", vs, vnote, mk.suspicious(iid, "NPC", bool(vnote))))
        ah, notes, unv = mk.ah_net(iid)
        if ah is not None:
            opts.append(("AH'ye koy", ah, "; ".join(notes), mk.suspicious(iid, "AH", unv)))
        ev = mk.de_ev(iid)
        if ev:
            opts.append(("DE et + mat sat", ev, "", mk.suspicious(iid, "DE", True)))

        for rid, rname, pid, cqty, color in known:
            if not pid:
                continue
            use, other_cost, missing, other_list = 0, 0, [], []
            for r, q in reag[rid]:
                if r == iid:
                    use += q
                elif r in conv and conv[r][0] == iid and pid not in conv:
                    use += q * conv[r][1]
                else:
                    c, where = mk.buy_cost(r)
                    if c is None:
                        missing.append(mk.name(r))
                    else:
                        other_cost += c * q
                        other_list.append(f"{q}× {mk.name(r)} ({where})")
            if not use:
                continue
            pv, how, pnotes, punv, _ = mk.best_value(pid)
            if pv is None or missing:
                opts.append((f"{rname} yap", None, "fiyatı bilinmeyen malzeme: " + ", ".join(missing) if missing
                             else "; ".join(pnotes), []))
                continue
            per_unit = (pv * (cqty or 1) - other_cost) / use
            craftable = have // use
            if craftable == 0:
                opts.append((f"{rname} yap", None, f"yetersiz: üretim başına {use} gerekli, {have} var", []))
                continue
            skill = {"orange": "beceri +1 kesin", "yellow": "beceri büyük ihtimalle +1",
                     "green": "beceri belki +1", "gray": ""}.get(color or "", "")
            note = f"ürünü {how}; {use} adet/üretim, elindekiyle {craftable} kez"
            if other_list:
                note += "; diğer: " + ", ".join(other_list)
            if skill:
                note += f"; {skill}"
            if pnotes:
                note += "; " + "; ".join(n for n in pnotes if n)
            opts.append((f"{rname} yap → {how}", per_unit, note, mk.suspicious(pid, how, punv)))

        valued = [o for o in opts if o[1] is not None]
        best = max(valued, key=lambda o: o[1]) if valued else None
        kind = ("Kumaş" if m.get("sub") == "Cloth" else
                "DE malzemesi" if iid in de.MAT_NAMES else
                "Yeşil/mavi item" if m.get("quality") in ("UNCOMMON", "RARE") and m.get("cls") in ("Weapon", "Armor") else
                "Diğer")
        sell_only = [o for o in valued if " yap" not in o[0]]
        safe = max(sell_only, key=lambda o: o[1]) if sell_only else None
        rows.append(dict(iid=iid, name=mk.name(iid), have=have, kind=kind, opts=opts, best=best, safe=safe))

    order = ["Kumaş", "DE malzemesi", "Yeşil/mavi item", "Diğer"]
    total_best = sum((r["best"][1] or 0) * r["have"] for r in rows if r["best"])
    total_safe = sum((r["safe"][1] or 0) * r["have"] for r in rows if r["safe"])
    snap = con.execute("SELECT MAX(last_modified) FROM snapshots WHERE house_id=?", (HOUSE_ID,)).fetchone()[0]
    L = [f"# Kendi malım — {datetime.now():%Y-%m-%d %H:%M}", "",
         f"Envanter: TSM SavedVariables, kayıt {sv['saved_at']:%Y-%m-%d %H:%M} (çanta+banka+posta; AH'dekiler hariç). "
         f"Güncel olması için oyunda `/reload`.",
         f"Fiyat: ev 113 snapshot {snap}. Satış hızı: TSM bölge. Değerler BİRİM başına net (AH payı + depozito riski düşülmüş).",
         f"Para (kayıttaki): " + ", ".join(f"{c}: {money(v)}" for c, v in sv["money"].items()), "",
         f"- Sadece satarak (NPC/AH/DE, üretim yok): ≈ **{money(total_safe)}**",
         f"- Üretim dahil en iyi seçeneklerle: ≈ **{money(total_best)}** (ÜST SINIR: her item bağımsız hesaplandı, "
         f"aynı tarif için malzemeler çakışabilir; ek malzeme alımı gerekebilir)",
         "- AH değeri = satılma ihtimali × (fiyat − %5) + satılmama ihtimali × (NPC fiyatı − depozito).",
         "- ⚠️ Uyarı sütunu: yavaş satış / aşırı arz / doğrulanmamış fiyat → önce 1-2 tane ile test et.", ""]
    for kind in order:
        part = [r for r in rows if r["kind"] == kind]
        if not part:
            continue
        L += [f"## {kind}", "",
              "| Item | ID | Adet | En iyi seçenek | Birim net | Toplam | Diğer seçenekler (birim net) | Notlar | ⚠️ Uyarı |",
              "|---|---|---|---|---|---|---|---|---|"]
        for r in sorted(part, key=lambda r: -((r["best"] or (0, 0))[1] or 0) * r["have"]):
            b = r["best"]
            others = "; ".join(f"{o[0]}: {money(o[1])}" for o in r["opts"] if o is not b)
            L.append(f"| {r['name']} | {r['iid']} | {r['have']} | **{b[0] if b else '—'}** | {money(b[1]) if b else '—'} | "
                     f"{money(b[1] * r['have']) if b else '—'} | {others} | {b[2] if b else ''} | "
                     f"{'; '.join(b[3]) if b else ''} |")
        L.append("")
    REPORTS_DIR.mkdir(exist_ok=True)
    out = REPORTS_DIR / f"own_goods_{datetime.now():%Y%m%d_%H%M}.md"
    out.write_text("\n".join(L), encoding="utf-8")
    print(f"{len(rows)} item, toplam en iyi değer {money(total_best)} → {out}")


if __name__ == "__main__":
    main()
