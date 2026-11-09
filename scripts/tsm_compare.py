"""TSM marketValue (realm) ile bizim piyasa değerimizi karşılaştırır; büyük farkları listeler.
Kullanım: python -m scripts.tsm_compare → reports/tsm_compare_<tarih>.md
"""
from datetime import datetime

from ahbot import tsm
from ahbot.analysis import market_values
from ahbot.config import REPORTS_DIR
from ahbot.db import connect

BIG_DIFF = 0.30          # |fark| / TSM ≥ %30 → büyük fark
MIN_QTY = 5              # bizde en az bu kadar adet listelenmiş olsun (tek ilanlık gürültüyü azalt)


def money(c):
    if c is None:
        return "—"
    c = int(round(c))
    g, s, b = c // 10000, (c // 100) % 100, c % 100
    return (f"{g}g " if g else "") + (f"{s}s " if g or s else "") + f"{b}c"


def main():
    con = connect()
    ours = market_values(con)
    t = tsm.latest(con)
    names = dict(con.execute("SELECT item_id, name FROM items"))
    lm = con.execute("SELECT MAX(last_modified) FROM snapshots").fetchone()[0]
    rows, both = [], 0
    for iid, (mv, qty, n, mn) in ours.items():
        tr = t.get(iid, {})
        tmv = tr.get("r_mv")
        if not tmv:
            continue
        both += 1
        diff = (mv - tmv) / tmv
        if abs(diff) >= BIG_DIFF and qty >= MIN_QTY:
            rows.append((abs(diff), iid, names.get(iid) or tr.get("name", ""), mv, tmv, tr.get("r_min"), mn, qty, n,
                         diff, tr.get("g_spd")))
    rows.sort(reverse=True)
    t_upd = next((v.get("tsm_realm_updated") for v in t.values() if v.get("tsm_realm_updated")), None)
    L = [f"# TSM marketValue ↔ bizim piyasa değeri — {datetime.now():%Y-%m-%d %H:%M}", "",
         f"Bizim snapshot: {lm} | TSM realm updatedAt: {t_upd}",
         "Bizim değer = adet ağırlıklı fiyatların alt %15–30 diliminin medyanı (tek snapshot). "
         "TSM marketValue birkaç günlük ağırlıklı ortalama → farkların bir kısmı zaman farkından.", "",
         f"Karşılaştırılan item: {both} | büyük fark (≥%{int(BIG_DIFF*100)}, ≥{MIN_QTY} adet): {len(rows)} "
         f"(%{100*len(rows)/max(both,1):.0f})", "",
         "| Item | ID | Bizim | TSM marketValue | Fark | Bizim min | TSM minBuyout | Adet / ilan | Bölge satış/gün |",
         "|---|---|---|---|---|---|---|---|---|"]
    for _, iid, name, mv, tmv, tmin, mn, qty, n, diff, spd in rows[:150]:
        L.append(f"| {name} | {iid} | {money(mv)} | {money(tmv)} | {diff:+.0%} | {money(mn)} | {money(tmin)} | "
                 f"{qty} / {n} | {'' if spd is None else f'{spd:g}'} |")
    REPORTS_DIR.mkdir(exist_ok=True)
    out = REPORTS_DIR / f"tsm_compare_{datetime.now():%Y%m%d_%H%M}.md"
    out.write_text("\n".join(L), encoding="utf-8")
    print(f"Karşılaştırılan {both}, büyük fark {len(rows)} → {out}")


if __name__ == "__main__":
    main()
