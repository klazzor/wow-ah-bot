"""Crafting kâr raporu + beceri geliştirme yolu (meslek planı adım 3).

Bölümler:
1. En kârlı 10 tarif (bilinen + öğrenilebilir)
2. Beceri yolu: Tailoring ve Enchanting için sonraki 25 puan (en ucuz / en kârlı sıra)
3. Bilinen tarifler (tam tablo)
4. Öğrenilebilir tarifler (Pattern/Formula item'ı olanlar; öğrenme maliyeti dahil)

Her tarif: malzeme maliyeti (AH en ucuz ve medyan; vendor'dan alındığı TSM geçmişinde görülen malzemeler vendor fiyatıyla),
ürünün AH / NPC / DE değeri, net kâr, TSM bölge soldPerDay, Forever'a özel işareti, şüpheli fırsat uyarıları.

Beceri rengi modeli (TAHMİNİ): renk = f(şu anki beceri − öğrenme seviyesi). Eşikler, kullanıcının ekran
görüntülerindeki gerçek renklerden meslek başına uydurulur; uyum oranı raporda yazılır.
Beceri artış ihtimali (tahmini): turuncu 1.0, sarı 0.6, yeşil 0.2, gri 0.

Kullanım: python -m scripts.crafting_report → reports/crafting_<tarih>.md
"""
from collections import defaultdict
from datetime import datetime
from itertools import product as iproduct

from ahbot import items as itemsmod
from ahbot.config import ENCHANTING_SKILL, REPORTS_DIR, TAILORING_SKILL
from ahbot.db import connect
from ahbot import savedvars
from ahbot.market import TEST_FIRST as mk_test_first
from ahbot.market import Market, money

SKILL = {"Tailoring": TAILORING_SKILL, "Enchanting": ENCHANTING_SKILL}
HORIZON = 25
P_SKILLUP = {"orange": 1.0, "yellow": 0.6, "green": 0.2, "gray": 0.0}
AH_DEMAND_FOR_PATH = 5     # beceri yolunda ürünü AH'de satmayı sadece bölge soldPerDay ≥ 5 ise say (yoksa NPC/DE)
from ahbot.analysis import SLOW_SOLD_PER_DAY as CRAFT_SLOW_SPD   # < 5/gün → "yavaş satış", sıralamada aşağı
COLOR_TR = {"orange": "turuncu", "yellow": "sarı", "green": "yeşil", "gray": "gri"}


def color_of(d, th):
    a, b, c = th
    return "orange" if d < a else "yellow" if d < b else "green" if d < c else "gray"


def fit_thresholds(obs):
    """obs: [(d, renk)] → hata sayısını en aza indiren (turuncu<a, sarı<b, yeşil<c) eşikleri."""
    best = None
    for a, b, c in iproduct(range(5, 61, 5), range(10, 101, 5), range(15, 141, 5)):
        if not a < b < c:
            continue
        err = sum(color_of(d, (a, b, c)) != col for d, col in obs)
        if best is None or err < best[0]:
            best = (err, (a, b, c))
    return best[1], 1 - best[0] / max(len(obs), 1)


def recipe_rows(con, mk, rows, reag):
    out = []
    for rid, prof, cat, name, pid, cqty, lvl, flag, teach, known, color in rows:
        cqty = cqty or 1
        cost_min = cost_med = 0
        missing, parts, rlist = [], [], []
        for r, q in reag[rid]:
            c, where = mk.buy_cost(r)
            cm, _ = mk.buy_cost(r, use_median=True)
            if c is None:
                missing.append(mk.name(r))
                continue
            cost_min += c * q
            cost_med += (cm or c) * q
            rlist.append((r, q, c))
            parts.append(f"{q}× {mk.name(r)} ({money(c)} {where})")
        row = dict(rid=rid, prof=prof, cat=cat, name=name, pid=pid, cqty=cqty, lvl=lvl, flag=flag, teach=teach,
                   known=known, color=color, rlist=rlist, slow=False, cost_min=None if missing else cost_min,
                   cost_med=None if missing else cost_med, missing=missing, parts=parts,
                   ah=None, npc=None, de=None, best=None, how=None, profit=None, spd=None, warn=[], notes=[])
        if pid:
            v, how, notes, unv, allv = mk.best_value(pid)
            row.update(ah=allv.get("AH"), npc=allv.get("NPC"), de=allv.get("DE"), how=how, notes=notes,
                       spd=mk.spd(pid), best=None if v is None else v * cqty)
            if row["best"] is not None and row["cost_min"] is not None:
                row["profit"] = row["best"] - row["cost_min"]
            row["warn"] = mk.suspicious(pid, how, unv, row["cost_min"], row["profit"])
            if how == "AH" and (row["spd"] is None or row["spd"] < CRAFT_SLOW_SPD):
                row["slow"] = True
                if not any(w.startswith("yavaş satış") for w in row["warn"]):
                    spd_txt = "bilinmiyor" if row["spd"] is None else f"{row['spd']:g}/gün"
                    row["warn"].insert(0, f"yavaş satış ({spd_txt} < {CRAFT_SLOW_SPD}, bölge)")
                    if mk_test_first not in row["warn"]:
                        row["warn"].append(mk_test_first)
        else:
            row["notes"] = ["enchant: item üretmez (sadece beceri / müşteri)"]
            if row["cost_min"] is not None:
                row["profit"] = -row["cost_min"]
        # öğrenme maliyeti
        row["learn_cost"], row["learn_src"] = 0, ""
        if not known and teach:
            ah = mk.ah_min(teach)
            vb = (mk.meta.get(teach) or {}).get("buy")
            if ah:
                row["learn_cost"], row["learn_src"] = ah, "AH"
            elif vb:
                row["learn_cost"], row["learn_src"] = vb, "vendor fiyatı (vendor'da satıldığı DOĞRULANMADI)"
            else:
                row["learn_cost"], row["learn_src"] = None, "fiyat yok (drop/quest olabilir)"
        out.append(row)
    return out


def path_value(r):
    """Beceri yolunda ürünün değeri: AH sadece yeterli talep varsa."""
    vals = [v for v in (r["npc"], r["de"]) if v]
    if r["ah"] and (r["spd"] or 0) >= AH_DEMAND_FOR_PATH:
        vals.append(r["ah"])
    return max(vals) * r["cqty"] if vals else 0


def _cash_for(r, k, inv_left):
    """k üretim için nakit malzeme maliyeti: önce envanter (nakit 0), eksik kalan AH/vendor fiyatıyla.
    Döner: (nakit, {item: envanterden kullanılan}, {item: satın alınacak})"""
    cash, used, buy = 0.0, {}, {}
    for i, q, c in r["rlist"]:
        need = k * q
        have = inv_left.get(i, 0)
        u = min(need, have)
        b = need - u
        if u:
            used[i] = u
        if b:
            buy[i] = b
            cash += b * c
    return cash, used, buy


def skill_path(prof, rows, th, inv_left):
    """Greedy: her beceri puanında beklenen NAKİT maliyeti en düşük tarif. Envanterdeki malzemeler önce kullanılır
    (nakit maliyeti 0; satış değeri ayrıca 'fırsat maliyeti' olarak raporlanır). inv_left yerinde güncellenir."""
    s0 = SKILL[prof]
    cands = [r for r in rows if r["prof"] == prof and r["lvl"] is not None and r["cost_min"] is not None
             and (r["known"] or r["learn_cost"] is not None)]
    learned, steps = set(), []
    for s in range(s0, s0 + HORIZON):
        best = None
        for r in cands:
            if r["lvl"] > s:
                continue
            p = P_SKILLUP[color_of(s - r["lvl"], th)]
            if r["known"] and r["color"] in P_SKILLUP:
                # Ekran görüntüsündeki gerçek renk şu anki beceride geçerli; beceri arttıkça renk ancak kötüleşir
                p = min(p, P_SKILLUP[r["color"]])
            if p == 0:
                continue
            k = 1 / p                                     # bu puan için beklenen üretim sayısı
            cash, used, buy = _cash_for(r, k, inv_left)
            income = path_value(r) * k                    # ürünleri satarsan beklenen gelir
            net = cash - income                           # bu puanın beklenen nakit maliyeti (negatif = kâr)
            learn = 0 if (r["known"] or r["rid"] in learned) else r["learn_cost"]
            usable = sum(1 for s2 in range(s, s0 + HORIZON) if P_SKILLUP[color_of(s2 - r["lvl"], th)] > 0
                         and not (r["known"] and P_SKILLUP.get(r["color"]) == 0))
            # 1. ölçüt: nakit; eşitlikte 2. ölçüt: toplam malzeme değeri (envanter dahil) → turuncu tarif sarıdan önce
            score = (round(net + learn / max(usable, 1)), k * r["cost_min"] - income)
            if best is None or score < best[0]:
                best = (score, r, k, net, learn, used, buy, cash, income)
        if not best:
            steps.append(dict(s=s, r=None))
            continue
        _, r, k, net, learn, used, buy, cash, income = best
        if learn:
            learned.add(r["rid"])
        for i, u in used.items():
            inv_left[i] -= u
        steps.append(dict(s=s, r=r, k=k, net=net + learn, learn=learn, used=used, buy=buy,
                          gross=cash + learn, income=income))
    # ardışık aynı tarifleri birleştir
    merged, total, tot_used, tot_buy = [], 0.0, defaultdict(float), defaultdict(float)
    gross = income = 0.0
    for st in steps:
        r = st["r"]
        if r is None:
            merged.append(dict(frm=st["s"], to=st["s"] + 1, r=None))
            continue
        total += st["net"]
        gross += st["gross"]
        income += st["income"]
        for i, u in st["used"].items():
            tot_used[i] += u
        for i, b in st["buy"].items():
            tot_buy[i] += b
        if merged and merged[-1]["r"] is r:
            m = merged[-1]
            m["to"] = st["s"] + 1
            m["crafts"] += st["k"]
            m["cost"] += st["net"]
            m["gross"] += st["gross"]
            m["learn"] += st["learn"]
            for i, u in st["used"].items():
                m["used"][i] += u
            for i, b in st["buy"].items():
                m["buy"][i] += b
        else:
            merged.append(dict(frm=st["s"], to=st["s"] + 1, r=r, crafts=st["k"], cost=st["net"], learn=st["learn"],
                               gross=st["gross"], used=defaultdict(float, st["used"]), buy=defaultdict(float, st["buy"])))
    return merged, total, tot_used, tot_buy, gross, income


def main():
    con = connect()
    rows = con.execute("""SELECT r.recipe_id, r.profession, r.category, r.name, r.crafted_item_id, r.crafted_qty_min,
                                 r.skill_level, r.forever_flag, r.teach_item_id,
                                 k.recipe_id IS NOT NULL, k.color
                          FROM recipes r LEFT JOIN known_recipes k USING(recipe_id)""").fetchall()
    # Öğrenilebilir: bilinmeyen, öğretici item'ı olan, seviyesi şu anki beceri + 25'e kadar
    rows = [r for r in rows if r[9] or (r[8] and r[6] is not None and r[6] <= SKILL[r[1]] + HORIZON)]
    reag = defaultdict(list)
    for rid, iid, q in con.execute("SELECT recipe_id, item_id, quantity FROM recipe_reagents"):
        reag[rid].append((iid, q))
    ids = set(r[4] for r in rows if r[4]) | set(r[8] for r in rows if r[8]) | {i for r in rows for i, _ in reag[r[0]]}
    itemsmod.ensure(ids)
    mk = Market(con)
    data = recipe_rows(con, mk, rows, reag)

    # Renk modeli: ekran görüntüsü renkleri (bilinen tarifler) ile uydur
    th, fit = {}, {}
    for prof in SKILL:
        obs = [(SKILL[prof] - r["lvl"], r["color"]) for r in data if r["prof"] == prof and r["known"]
               and r["color"] and r["lvl"] is not None]
        th[prof], fit[prof] = fit_thresholds(obs)

    def warn_txt(r):
        w = list(r["warn"])
        if r["flag"]:
            w.insert(0, f"🆕 Forever'a özel ({r['flag']}) — rekabet az olabilir")
        return "; ".join(w)

    def row_md(r, learn=False):
        base = (f"| {r['name']} | {r['prof'][:4]} | {r['lvl'] if r['lvl'] is not None else '?'} | "
                f"{COLOR_TR.get(r['color'], '') if r['known'] else COLOR_TR[color_of(SKILL[r['prof']] - (r['lvl'] or 0), th[r['prof']])] + '*'} | "
                f"{money(r['cost_min'])} / {money(r['cost_med'])} | {money(r['ah'] and r['ah'] * r['cqty'])} | "
                f"{money(r['npc'] and r['npc'] * r['cqty'])} | {money(r['de'] and r['de'] * r['cqty'])} | "
                f"**{money(r['profit'])}** ({r['how'] or '—'}) | {'' if r['spd'] is None else f'{r['spd']:g}'} | ")
        if learn:
            base += f"{money(r['learn_cost'])} {r['learn_src']} | "
        miss = f"eksik fiyat: {', '.join(r['missing'])}; " if r["missing"] else ""
        return base + f"{miss}{warn_txt(r)} |"

    head = ("| Tarif | Meslek | Öğr. sev. | Renk | Malzeme (en ucuz / medyan) | Ürün AH | Ürün NPC | Ürün DE | "
            "Net kâr (en iyi) | Satış/gün | ")

    snap = con.execute("SELECT MAX(last_modified) FROM snapshots").fetchone()[0]
    L = [f"# Crafting kâr raporu — {datetime.now():%Y-%m-%d %H:%M}", "",
         f"Beceri: Tailoring {TAILORING_SKILL}, Enchanting {ENCHANTING_SKILL}. Fiyat: ev 113 snapshot {snap}. "
         "Satış/gün: TSM bölge. Değerler üretim başına (ürün adedi dahil), AH değeri satılma ihtimaliyle ağırlıklı.",
         "Malzeme: vendor'dan alındığı TSM geçmişinde görülenler vendor fiyatıyla, diğerleri AH (en ucuz / medyan).",
         "Renk: bilinen tariflerde ekran görüntüsünden; `*` = model tahmini.",
         "⚠️ Uyarılar: yavaş satış / aşırı arz / doğrulanmamış fiyat / DE tablosu → önce 1-2 tane ile test et.", ""]

    # 1. En kârlı 10
    prof_rows = [r for r in data if r["profit"] is not None and r["pid"]]
    # Yavaş satanlar (AH'de satılan, bölge soldPerDay < 5) her zaman hızlı satanların altında
    top = sorted(prof_rows, key=lambda r: (r["slow"], -(r["profit"] - (r["learn_cost"] or 0))))[:10]
    L += ["## 1. En kârlı 10 tarif (sıralama: önce satış hızı yeterli olanlar, sonra net kâr − öğrenme maliyeti)", "",
          f"🐢 = yavaş satış: ürün AH'de satılıyor ve bölge soldPerDay < {CRAFT_SLOW_SPD} → sıralamada aşağıda.", "",
          head + "Öğrenme | Uyarı |", "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in top:
        label = f"{'🐢 ' if r['slow'] else ''}{r['name']}{'' if r['known'] else ' (öğrenilebilir)'}"
        L.append(row_md(r, learn=True).replace(f"| {r['name']} |", f"| {label} |", 1))
    L.append("")

    # 2. Beceri yolu
    inv, sv = savedvars.inventory()
    inv_left = {i: float(q) for i, q in inv.items()}
    L += ["## 2. Beceri yolu (sonraki 25 puan)", "",
          "Model TAHMİNİ: renk eşikleri ekran görüntülerinden uyduruldu; beceri artış ihtimali turuncu 1.0 / sarı 0.6 / "
          "yeşil 0.2. Ürün değeri: NPC ya da DE; AH sadece bölge satış ≥ 5/gün ise. Trainer'dan öğrenilen ama "
          "seviyesi bilinmeyen tarifler dahil değil.",
          f"**Envanter önce kullanılır** (TSM SavedVariables, kayıt {sv['saved_at']:%Y-%m-%d %H:%M}; nakit maliyeti 0); "
          "eksik kalan AH/vendor fiyatıyla alınır. 'Nakit maliyet' = cebinden çıkacak para. Envanterden kullanılan "
          "malların satış değeri ayrıca 'fırsat maliyeti' olarak verilir (onları satsaydın kazanacağın).", ""]
    fmt_q = lambda d: ", ".join(f"{q:.0f}× {mk.name(i)}" for i, q in sorted(d.items(), key=lambda x: -x[1]) if q >= 0.5) or "—"
    for prof in SKILL:
        a, b, c = th[prof]
        merged, total, used, buy, gross, income = skill_path(prof, data, th[prof], inv_left)
        opp = sum(q * max(0, mk.best_value(i)[0] or 0) for i, q in used.items())
        L += [f"### {prof} {SKILL[prof]} → {SKILL[prof] + HORIZON}",
              f"Renk modeli (fark = beceri − öğrenme sev.): turuncu < {a}, sarı < {b}, yeşil < {c}, sonra gri. "
              f"Ekran görüntüsü renkleriyle uyum: %{fit[prof]*100:.0f}.", "",
              "| Beceri | Tarif | Durum | Beklenen üretim | Envanterden | Satın alınacak | Brüt alım | Net (ürün satışı sonrası; − = kâr) | Not |",
              "|---|---|---|---|---|---|---|---|---|"]
        for m in merged:
            r = m["r"]
            if r is None:
                L.append(f"| {m['frm']}→{m['to']} | — | | | | | | | bu aralıkta veri olan tarif yok (trainer'a bak) |")
                continue
            durum = "biliniyor" if r["known"] else f"öğren: {money(m['learn'])} ({r['learn_src']})"
            L.append(f"| {m['frm']}→{m['to']} | {r['name']} | {durum} | ~{m['crafts']:.1f} | {fmt_q(m['used'])} | "
                     f"{fmt_q(m['buy'])} | {money(m['gross'])} | {money(m['cost'])} | "
                     f"malzeme/üretim: {', '.join(r['parts'])}; {warn_txt(r)} |")
        L += ["",
              f"- **Brüt alım (cebinden önce çıkacak): {money(gross)}** — alışveriş listesi: {fmt_q(buy)}",
              f"- Ürünleri satarsan beklenen gelir: {money(income)} → **net: {money(total)}** (negatif = kâr; "
              f"aynı üründen çok sayıda satmak fiyatı düşürebilir)",
              f"- Envanterden kullanılan: {fmt_q(used)} (bunları satsaydın ≈ {money(opp)} — fırsat maliyeti)", ""]

    # 3-4. Tam tablolar
    for title, sel, learn in (("3. Bilinen tarifler", lambda r: r["known"], False),
                              ("4. Öğrenilebilir tarifler (Pattern/Formula)", lambda r: not r["known"], True)):
        part = sorted([r for r in data if sel(r)], key=lambda r: (r["prof"], -(r["profit"] if r["profit"] is not None else -1e9)))
        L += [f"## {title} ({len(part)})", "",
              head + ("Öğrenme | " if learn else "") + "Uyarı |",
              "|---|---|---|---|---|---|---|---|---|---|" + ("---|" if learn else "") + "---|"]
        L += [row_md(r, learn) for r in part]
        L.append("")

    REPORTS_DIR.mkdir(exist_ok=True)
    out = REPORTS_DIR / f"crafting_{datetime.now():%Y%m%d_%H%M}.md"
    out.write_text("\n".join(L), encoding="utf-8")
    print(f"Tarif: {len(data)} (bilinen {sum(r['known'] for r in data)}, öğrenilebilir {sum(not r['known'] for r in data)})")
    for prof in SKILL:
        print(f"{prof}: renk eşikleri {th[prof]}, uyum %{fit[prof]*100:.0f}")
    print(f"→ {out}")


if __name__ == "__main__":
    main()
