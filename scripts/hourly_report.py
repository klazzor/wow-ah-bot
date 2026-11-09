"""Saatlik özet rapor (tek sayfa). hourly.py veri toplandıktan sonra çağırır.

Çıktı: reports/latest.md (her saat üzerine yazılır) + reports/hourly/YYYYMMDD_HH.md (3 gün saklanır).
Durum (önceki rapora göre değişiklikler için): data/hourly_state.json

Bölümler: 1 veri sağlığı · 2 envanter (ilk 10) · 3 fiyat alarmı · 4 satış zamanlaması · 5 Forever'a özel · 6 değişiklikler
· 7 defter (TSM Accounting, son 24 sa)
Kurallar: şüpheli fırsat (ahbot.market.suspicious) + soldPerDay < 5 yavaş satış.

Kullanım: python -m scripts.hourly_report
"""
import json
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

from ahbot import savedvars
from ahbot.analysis import SLOW_SOLD_PER_DAY
from ahbot.config import AH_CUT_FACTION, DEFAULT_LIST_HOURS, DEPOSIT_FACTOR, HOUSE_ID, REPORTS_DIR
from ahbot.db import connect
from ahbot.market import Market, money

HOURLY_DIR = REPORTS_DIR / "hourly"
STATE = Path("data/hourly_state.json")
LOG = Path("data/logs/collector.log")
KEEP_DAYS = 3

ALARM_MIN_SPD = 20          # fiyat alarmı: bölge soldPerDay ≥ 20
ALARM_DROP = 0.30           # şu anki en düşük ≤ 7 günlük medyan × 0.70
ALARM_MIN_SNAPSHOTS = 12    # 7 günlük medyan için en az bu kadar snapshot
ALARM_MAX = 10
BUY_CAP = 0.25              # tek alım mevcut paranın %25'ini geçmesin
TIMING_MIN_HOURS = 48
FOREVER_ID_MIN = 200000     # SEZGİSEL: Classic item ID'leri < ~25k; SoD/Forever yeni item'ları 200k+ (ör. Cerulean Dye 249409)
BIG_MOVE = 0.25             # değişiklikler: %25+ fiyat hareketi
INV_STALE_H = 12
RESET_MULT = 3              # fiyat referansın (önceki snapshot / 7g medyan / TSM) 3 katını aşarsa: piyasa sıfırlama şüphesi
RESET_WARN = "⚠️ piyasa sıfırlama (reset) şüphesi"
SLOW_AH_MULT = 2            # yavaş satan item: AH'de satılırsa net ≥ 2× NPC ise karar "AH 🐢" (NPC yedek)
SLOW_AH_TRIES = 3
SLOW_AH_LABEL = "AH 🐢 (2-3 kez dene, satılmazsa NPC)"


def now_utc():
    return datetime.now(timezone.utc)


def age_txt(dt):
    if dt is None:
        return "—"
    h = (now_utc() - dt).total_seconds() / 3600
    return f"{h:.1f} sa" if h < 48 else f"{h/24:.1f} gün"


def lm_dt(s):
    try:
        return parsedate_to_datetime(s)
    except (TypeError, ValueError):
        return None


def duration_for(spd):
    """İlan süresi satış hızına göre: hızlı satan kısa (depozito düşük), yavaş satan uzun."""
    if spd is None or spd < SLOW_SOLD_PER_DAY:
        return 24
    if spd < ALARM_MIN_SPD:
        return 8
    return 2


# ---------- veri ----------
def snapshot_series(con, kind=None, days=7):
    """[(snapshot_id, kind, dt_utc)] son `days` gün."""
    out = []
    q = "SELECT id, kind, last_modified FROM snapshots WHERE house_id=?" + (" AND kind=?" if kind else "")
    for sid, k, lm in con.execute(q, (HOUSE_ID, kind) if kind else (HOUSE_ID,)):
        dt = lm_dt(lm)
        if dt and now_utc() - dt <= timedelta(days=days):
            out.append((sid, k, dt))
    return sorted(out, key=lambda x: x[2])


def price_history(con, item_ids, days=7, field="min_unit"):
    """{item_id: [(dt_utc, fiyat)]} — field: min_unit (alım fiyatı) ya da p10_unit (gürültüye dayanıklı)"""
    assert field in ("min_unit", "p10_unit", "median_unit")
    snaps = snapshot_series(con, days=days)
    if not snaps or not item_ids:
        return {}
    by_sid = {sid: dt for sid, _, dt in snaps}
    ids = list(item_ids)
    hist = defaultdict(list)
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        q = (f"SELECT snapshot_id, item_id, {field} FROM item_summary WHERE {field} IS NOT NULL "
             f"AND item_id IN ({','.join('?' * len(chunk))}) AND snapshot_id IN ({','.join('?' * len(by_sid))})")
        for sid, iid, mn in con.execute(q, (*chunk, *by_sid)):
            hist[iid].append((by_sid[sid], mn))
    for v in hist.values():
        v.sort()
    return hist


def reset_check(mk, iid, price, h):
    """Fiyat bir saatte sıçradıysa (biri ucuz ilanları toplayıp yeniden fiyatladıysa) referansı döndürür.
    h: [(dt, fiyat)] — son eleman şu anki snapshot. Referans = önceki snapshot, 7g medyan, TSM realm marketValue,
    TSM bölge avgSalePrice'tan mevcut olanların medyanı. price > RESET_MULT × referans → (referans, açıklama), yoksa None."""
    if not price:
        return None
    refs = {}
    if len(h) >= 2 and h[-2][1]:
        refs["önceki snapshot"] = h[-2][1]
    if len(h) >= 3:
        refs["7g medyan"] = statistics.median(p for _, p in h)
    t = mk.tsm.get(iid) or {}
    if t.get("r_mv"):
        refs["TSM marketValue"] = t["r_mv"]
    if t.get("g_avg"):
        refs["TSM avgSalePrice"] = t["g_avg"]
    if not refs:
        return None
    ref = statistics.median(refs.values())
    if price <= RESET_MULT * ref:
        return None
    return ref, ", ".join(f"{k} {money(v)}" for k, v in refs.items())


def slow_ah_option(mk, iid, spd, rc):
    """Karar NPC çıkmış yavaş satan item için AH denemesi önerilir mi? (20261109-1240-1)
    Koşul: bölge soldPerDay < SLOW_SOLD_PER_DAY ve AH'de satılırsa net birim (%5 kesinti sonrası) ≥ SLOW_AH_MULT × NPC.
    Not: TSM saleRate ile hesaplanan beklenen değer çoğu zaman NPC'nin altında kalır (saleRate tüm ilanları sayar,
    pahalı ilanlar oranı düşürür); bu yüzden ölçüt 'satılırsa net' — risk uyarıda gösterilir.
    → (ilan fiyatı, satılırsa net, tek ilanın beklenen değeri, 24 sa birim depozito) ya da None"""
    if spd is None or spd >= SLOW_SOLD_PER_DAY:
        return None
    vend, _ = mk.vendor_sell(iid)
    mn = mk.ah_min(iid)
    if not vend or not mn:
        return None
    price = round(rc[0]) if rc else mn - 1
    sold = price * (1 - AH_CUT_FACTION)
    if sold < SLOW_AH_MULT * vend:
        return None
    ev, _, _ = mk.ah_net(iid, price)
    dep = ((mk.meta.get(iid) or {}).get("sell") or 0) * DEPOSIT_FACTOR[24]
    return price, sold, ev, dep


# ---------- bölümler ----------
def sec_health(con):
    L, problems = ["## 1. Veri sağlığı"], []
    rows = []
    for kind in ("items", "commodities"):
        r = con.execute("SELECT last_modified, fetched_ts, n_auctions FROM snapshots WHERE house_id=? AND kind=? "
                        "ORDER BY id DESC LIMIT 1", (HOUSE_ID, kind)).fetchone()
        if not r:
            problems.append(f"{kind} snapshot yok")
            continue
        dt = lm_dt(r[0])
        rows.append(f"{kind}: {r[0]} ({age_txt(dt)} önce, {r[2]} ilan)")
        if dt and now_utc() - dt > timedelta(hours=2):
            problems.append(f"{kind} snapshot {age_txt(dt)} eski")
    for tbl, limit_h in (("tsm_realm", 6), ("tsm_region", 72)):
        ua = con.execute(f"SELECT MAX(updated_at) FROM {tbl}").fetchone()[0]
        dt = datetime.fromisoformat(ua.replace("Z", "+00:00")) if ua else None
        last = con.execute("SELECT fetched_ts, http_status FROM fetch_log WHERE source=? ORDER BY fetched_ts DESC LIMIT 1",
                           (tbl,)).fetchone()
        rows.append(f"{tbl}: veri {age_txt(dt)} önce" + (f", son indirme {last[0][11:16]} UTC (HTTP {last[1]})" if last else ""))
        if dt is None or now_utc() - dt > timedelta(hours=limit_h):
            problems.append(f"{tbl} verisi eski ({age_txt(dt)})")
        if last and last[1] != 200:
            problems.append(f"{tbl} son indirme HTTP {last[1]}")
    # collector log: son çalıştırmadaki hatalar
    if LOG.exists():
        text = LOG.read_text(encoding="utf-8", errors="replace").split("\n=== ")
        lastrun = text[-1] if text else ""
        errs = [l.strip() for l in lastrun.splitlines() if any(k in l for k in ("HATA", "Error", "başarısız", "Traceback"))]
        if errs:
            problems.append("son görev: " + " | ".join(errs[:3]))
    L.append("- " + " · ".join(rows))
    L.append("- " + ("⚠️ " + "; ".join(problems) if problems else "✅ sorun yok"))
    return L, problems


def sec_inventory(con, mk, sv, inv):
    L = ["## 2. Envanterim (en değerli 10)"]
    hist = price_history(con, list(inv), days=7)
    saved = sv["saved_at"].astimezone(timezone.utc) if sv["saved_at"].tzinfo else sv["saved_at"].astimezone().astimezone(timezone.utc)
    money_now = sum(sv["money"].values())
    stale = now_utc() - saved > timedelta(hours=INV_STALE_H)
    L.append(f"Kayıt: {sv['saved_at']:%d.%m %H:%M} ({age_txt(saved)} önce) · Para: **{money(money_now)}**"
             + (f" · ⚠️ envanter {INV_STALE_H} saatten eski — oyunda `/reload`" if stale else ""))
    items = []
    for iid, q in inv.items():
        mn = mk.ah_min(iid)
        rc = reset_check(mk, iid, mn, hist.get(iid, [])) if mn else None
        # Reset şüphesinde AH değeri (ve karar) referans fiyatla hesaplanır; şişmiş min fiyat kullanılmaz
        v, how, notes, unv, allv = mk.best_value(iid, ah_price=round(rc[0]) if rc else None)
        if v and v > 0:
            items.append((v * q, iid, q, v, how, unv, rc))
    items.sort(key=lambda x: x[:2], reverse=True)
    L += ["", "| Item | Adet | Karar | Önerilen fiyat (birim) | Süre | Beklenen toplam (satılmama riski dahil) | Uyarı |",
          "|---|---|---|---|---|---|---|"]
    state = {}
    for tot, iid, q, v, how, unv, rc in items[:10]:
        spd = mk.spd(iid)
        if how == "AH":
            mn = mk.ah_min(iid)
            price = (mn - 1) if mn else (mk.tsm.get(iid) or {}).get("r_mv")
            dur = duration_for(spd)
            dep = ((mk.meta.get(iid) or {}).get("sell") or 0) * DEPOSIT_FACTOR[dur] * q
            ptxt = f"{money(rc[0])} (referans; şu anki min {money(mn)})" if rc else money(price)
            dtxt = f"{dur} sa (depozito ≈ {money(dep)})"
        elif how == "NPC" and (slow := slow_ah_option(mk, iid, spd, rc)):
            # Yavaş satan ama AH fiyatı NPC'nin çok üstünde: AH'de 2-3 kez dene, satılmazsa NPC
            how = SLOW_AH_LABEL
            price, ah_sold, ah_ev, dep1 = slow
            ptxt = (f"AH {money(price)} (satılırsa net {money(ah_sold)}) · yedek NPC {money(v)}"
                    + (f" · referans; şu anki min {money(mk.ah_min(iid))}" if rc else ""))
            dtxt = f"24 sa (depozito ≈ {money(dep1 * q)}, satılmazsa yanar)"
        elif how == "NPC":
            ptxt, dtxt = money(v) + " (NPC)", "—"
        else:
            ptxt, dtxt = "DE et", "—"
        w = mk.suspicious(iid, "AH" if how == SLOW_AH_LABEL else how, unv)
        if how == SLOW_AH_LABEL:
            w.insert(0, f"TSM saleRate'e göre tek ilanın beklenen değeri {money(slow[2])} (< NPC); "
                        f"{SLOW_AH_TRIES} denemede satılmazsa NPC'ye sat")
        if rc:
            w.insert(0, f"{RESET_WARN}: min {money(mk.ah_min(iid))} > {RESET_MULT}× referans {money(rc[0])} ({rc[1]})")
        L.append(f"| {mk.name(iid)} | {q} | **{how}** | {ptxt} | {dtxt} | {money(tot)} | {'; '.join(w)} |")
        state[str(iid)] = dict(how=how, value=v)
    return L, state, money_now


def sec_alarms(con, mk, money_now):
    L = ["## 3. Fiyat alarmı", f"Kriter: bölge satış ≥ {ALARM_MIN_SPD}/gün ve en düşük fiyat ≤ kendi 7 günlük medyanının "
         f"%{int((1-ALARM_DROP)*100)}'i."
         + (f" Alım üst sınırı: paranın %{int(BUY_CAP*100)}'i = {money(money_now * BUY_CAP)}." if money_now is not None else "")]
    cands = [iid for iid, t in mk.tsm.items() if (t.get("g_spd") or 0) >= ALARM_MIN_SPD and mk.ah_min(iid)]
    hist = price_history(con, cands)
    alarms, thin = [], 0
    sid_latest = [r[0] for r in con.execute("SELECT MAX(id) FROM snapshots WHERE house_id=? GROUP BY kind", (HOUSE_ID,))]
    for iid in cands:
        h = hist.get(iid, [])
        if len(h) < ALARM_MIN_SNAPSHOTS:
            thin += 1
            continue
        med = statistics.median(p for _, p in h)
        cur = mk.ah_min(iid)
        if cur <= med * (1 - ALARM_DROP):
            thr = med * (1 - ALARM_DROP)
            avail = con.execute(
                f"SELECT COALESCE(SUM(quantity),0) FROM auction_seen WHERE item_id=? AND unit_price<=? "
                f"AND snapshot_id IN ({','.join('?' * len(sid_latest))})", (iid, thr, *sid_latest)).fetchone()[0]
            cap_qty = (int(money_now * BUY_CAP // cur) if cur else 0) if money_now is not None else avail
            alarms.append((cur / med, iid, cur, med, avail, min(avail, cap_qty)))
    alarms.sort()
    if not alarms:
        L.append(f"- Alarm yok." + (f" ({thin} aday için henüz {ALARM_MIN_SNAPSHOTS} snapshot'lık geçmiş yok.)" if thin else ""))
        return L, {}
    L += ["", "| Item | Şu an | 7g medyan | Fark | Ucuz adet | Önerilen en fazla | Uyarı |", "|---|---|---|---|---|---|---|"]
    st = {}
    for ratio, iid, cur, med, avail, qty in alarms[:ALARM_MAX]:
        w = mk.suspicious(iid, "AH", False)
        qtxt = f"{qty} adet ({money(qty * cur)})" if qty else "— (paranın %25'ini aşar)"
        L.append(f"| {mk.name(iid)} | {money(cur)} | {money(med)} | {(ratio-1)*100:+.0f}% | {avail} | {qtxt} | {'; '.join(w)} |")
        st[str(iid)] = cur
    if thin:
        L.append(f"\n({thin} aday için yeterli geçmiş yok.)")
    return L, st


def sec_timing(con, mk, inv):
    snaps = snapshot_series(con, days=14)
    span_h = (snaps[-1][2] - snaps[0][2]).total_seconds() / 3600 if len(snaps) > 1 else 0
    L = ["## 4. Satış zamanlaması"]
    if span_h < TIMING_MIN_HOURS:
        L.append(f"- Henüz yok ({span_h:.0f} saat veri; {TIMING_MIN_HOURS} saat gerekli).")
        return L
    comm = sorted((iid for iid, s in mk.summ.items() if (mk.tsm.get(iid) or {}).get("g_spd")),
                  key=lambda i: -(mk.tsm[i]["g_spd"]))[:20]
    ids = list(dict.fromkeys(list(inv) + comm))
    hist = price_history(con, ids, days=14)
    L += ["| Item | En yüksek (yerel saat) | En düşük | Fark |", "|---|---|---|---|"]
    rows = []
    for iid in ids:
        h = hist.get(iid, [])
        if len(h) < 24:
            continue
        mean = statistics.mean(p for _, p in h)
        by_hour = defaultdict(list)
        for dt, p in h:
            by_hour[dt.astimezone().hour // 3].append(p / mean)
        avg = {b: statistics.mean(v) for b, v in by_hour.items() if len(v) >= 2}
        if len(avg) < 3:
            continue
        hi, lo = max(avg, key=avg.get), min(avg, key=avg.get)
        rows.append((avg[hi] - avg[lo], iid, hi, lo))
    for d, iid, hi, lo in sorted(rows, reverse=True)[:10]:
        L.append(f"| {mk.name(iid)} | {hi*3:02d}–{hi*3+3:02d} | {lo*3:02d}–{lo*3+3:02d} | %{d*100:.0f} |")
    if not rows:
        L.append("| — | yeterli saatlik dağılım yok | | |")
    return L


def sec_forever(con, mk):
    L = ["## 5. Forever'a özel item'lar (son 24 saatte en çok değişen 5)",
         f"Tanım SEZGİSEL: item ID ≥ {FOREVER_ID_MIN} (Classic'te olmayan yeni item'lar). Fiyat = adet ağırlıklı alt %10 "
         "(en ucuz tek ilandan daha sağlam); en az 5 ilanı olanlar."]
    ids = [i for i in mk.summ if i >= FOREVER_ID_MIN and (mk.summ[i][3] or 0) >= 5]
    hist7 = price_history(con, ids, days=7, field="p10_unit")
    rows = []
    for iid in ids:
        h7 = hist7.get(iid, [])
        if len(h7) < 2:
            continue
        cur_dt, cur = h7[-1]
        h = [x for x in h7 if cur_dt - x[0] <= timedelta(days=2)]
        older = [x for x in h if cur_dt - x[0] >= timedelta(hours=20)]
        base_dt, base = (older[-1] if older else h[0])
        if base:
            rows.append((abs(cur / base - 1), iid, base, cur, (cur_dt - base_dt).total_seconds() / 3600,
                         reset_check(mk, iid, cur, h7)))
    if not rows:
        L.append("- Karşılaştırılacak geçmiş yok.")
        return L
    L += ["| Item | Önce | Şimdi | Değişim | Aralık | Satış/gün | Uyarı |", "|---|---|---|---|---|---|---|"]
    for d, iid, base, cur, hrs, rc in sorted(rows, key=lambda x: x[:2], reverse=True)[:5]:
        spd = mk.spd(iid)
        warn = f"{RESET_WARN}: > {RESET_MULT}× referans {money(rc[0])} ({rc[1]})" if rc else ""
        L.append(f"| {mk.name(iid)} | {money(base)} | {money(cur)} | {(cur/base-1)*100:+.0f}% | {hrs:.0f} sa | "
                 f"{'—' if spd is None else f'{spd:g}'}{' 🐢' if spd is not None and spd < SLOW_SOLD_PER_DAY else ''} | {warn} |")
    return L


def sec_ledger(con, mk, hours=24):
    """Son `hours` saatin TSM Accounting özeti (ayrıntılı yorum saatlik analistte)."""
    L = [f"## 7. Defter (TSM Accounting, son {hours} saat)"]
    have = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'tsm_%'")}
    if not {"tsm_sales", "tsm_buys"} <= have:
        L.append("- Defter verisi yok (ahbot.ledger henüz çalışmadı).")
        return L
    since = int((now_utc() - timedelta(hours=hours)).timestamp())

    def agg(table, src):
        return con.execute(f"SELECT COUNT(*), COALESCE(SUM(quantity),0), COALESCE(SUM(total),0) FROM {table} "
                           f"WHERE ts>=? AND source=?", (since, src)).fetchone()
    s_ah, s_v, b_ah, b_v = agg("tsm_sales", "Auction"), agg("tsm_sales", "Vendor"), agg("tsm_buys", "Auction"), agg("tsm_buys", "Vendor")
    s_other = con.execute("SELECT COALESCE(SUM(total),0) FROM tsm_sales WHERE ts>=? AND source NOT IN ('Auction','Vendor')",
                          (since,)).fetchone()[0]
    b_other = con.execute("SELECT COALESCE(SUM(total),0) FROM tsm_buys WHERE ts>=? AND source NOT IN ('Auction','Vendor')",
                          (since,)).fetchone()[0]
    cut = int(s_ah[2] * AH_CUT_FACTION)
    # Yanan depozito: süresi dolan + iptal edilen ilanlar; vendor fiyatı × katsayı (süre bilinmiyor → varsayılan süre) — TAHMİNİ
    lost, n_exp, n_can = 0, 0, 0
    for table in ("tsm_expired", "tsm_cancelled"):
        if table not in have:
            continue
        for iid, q in con.execute(f"SELECT item_id, quantity FROM {table} WHERE ts>=?", (since,)):
            lost += ((mk.meta.get(iid) or {}).get("sell") or 0) * DEPOSIT_FACTOR[DEFAULT_LIST_HOURS] * (q or 0)
            if table == "tsm_expired":
                n_exp += 1
            else:
                n_can += 1
    exp = con.execute("SELECT COALESCE(SUM(amount),0) FROM tsm_expense WHERE ts>=?", (since,)).fetchone()[0] \
        if "tsm_expense" in have else 0
    sales = s_ah[2] + s_v[2] + s_other
    buys = b_ah[2] + b_v[2] + b_other
    net = sales - cut - lost - buys
    L.append(f"- Satış: AH {s_ah[0]} kayıt / {s_ah[1]} adet / {money(s_ah[2])} · NPC {s_v[0]} kayıt / {money(s_v[2])}"
             + (f" · diğer {money(s_other)}" if s_other else ""))
    L.append(f"- Alım: AH {b_ah[0]} kayıt / {b_ah[1]} adet / {money(b_ah[2])} · vendor {b_v[0]} kayıt / {money(b_v[2])}"
             + (f" · diğer {money(b_other)}" if b_other else ""))
    L.append(f"- Net: satış {money(sales)} − %{AH_CUT_FACTION*100:g} kesinti {money(cut)} − yanan depozito ≈ {money(lost)} "
             f"− alım {money(buys)} = **{money(net)}**" + (f" (ayrıca diğer giderler, ör. tamir: {money(exp)})" if exp else ""))
    L.append(f"- Süresi dolan ilan: {n_exp} · iptal: {n_can}"
             + (f" (depozito tahmini: süre bilinmiyor, {DEFAULT_LIST_HOURS} sa varsayıldı)" if lost else ""))
    return L


def sec_changes(prev, cur, mk):
    L = ["## 6. Önceki rapora göre değişiklikler"]
    if not prev:
        L.append("- İlk rapor, karşılaştırma yok.")
        return L
    out = []
    pa, ca = set(prev.get("alarms", {})), set(cur["alarms"])
    out += [f"🔔 yeni alarm: {mk.name(int(i))}" for i in ca - pa]
    out += [f"🔕 alarm kalktı: {mk.name(int(i))}" for i in pa - ca]
    for i, p in cur["prices"].items():
        q = prev.get("prices", {}).get(i)
        if q and p and abs(p / q - 1) >= BIG_MOVE:
            out.append(f"📈📉 {mk.name(int(i))}: {money(q)} → {money(p)} ({(p/q-1)*100:+.0f}%)")
    for i, d in cur["inv"].items():
        pd = prev.get("inv", {}).get(i)
        if pd and pd["how"] != d["how"]:
            out.append(f"🔁 {mk.name(int(i))}: karar {pd['how']} → {d['how']}")
    if prev.get("money") is not None and cur["money"] != prev["money"]:
        out.append(f"💰 para: {money(prev['money'])} → {money(cur['money'])}")
    L += [f"- {x}" for x in out[:12]] if out else ["- Önemli değişiklik yok."]
    return L


def main():
    import time
    t0 = time.time()
    con = connect()
    mk = Market(con)
    have_sv = savedvars.available()
    inv, sv = savedvars.inventory() if have_sv else ({}, None)
    prev = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else None

    L = [f"# AH özeti — {datetime.now():%d.%m.%Y %H:%M} (ev {HOUSE_ID}, PvP Horde)", "",
         "Ayrıntı: `reports/` içindeki own_goods / crafting / disenchant / vendor_snipe raporları. "
         f"⚠️ = şüpheli fırsat kuralı, 🐢 = satış < {SLOW_SOLD_PER_DAY:g}/gün (bölge) → önce 1-2 tane ile test et.", ""]
    h, _ = sec_health(con)
    L += h + [""]
    if have_sv:
        inv_l, inv_state, money_now = sec_inventory(con, mk, sv, inv)
        L += inv_l + [""]
    else:   # açık mod (GitHub Actions): envanter/para yok
        inv_state, money_now = {}, None
    al_l, al_state = sec_alarms(con, mk, money_now)
    L += al_l + [""]
    L += sec_timing(con, mk, inv) + [""]
    L += sec_forever(con, mk) + [""]
    tracked = set(map(int, inv_state)) | set(map(int, al_state))
    cur = dict(alarms=al_state, inv=inv_state, money=money_now,
               prices={str(i): mk.ah_min(i) for i in tracked if mk.ah_min(i)})
    L += sec_changes(prev, cur, mk)
    if have_sv:
        L += [""] + sec_ledger(con, mk)
    L += ["", f"_Üretim süresi: {time.time() - t0:.1f} sn_"]

    text = "\n".join(L) + "\n"
    REPORTS_DIR.mkdir(exist_ok=True)
    HOURLY_DIR.mkdir(parents=True, exist_ok=True)
    (REPORTS_DIR / "latest.md").write_text(text, encoding="utf-8")
    (HOURLY_DIR / f"{datetime.now():%Y%m%d_%H}.md").write_text(text, encoding="utf-8")
    STATE.write_text(json.dumps(cur, ensure_ascii=False), encoding="utf-8")
    # 3 günden eski saatlik raporlar (sadece reports/hourly/YYYYMMDD_HH.md adlı dosyalar)
    cutoff = datetime.now() - timedelta(days=KEEP_DAYS)
    for f in HOURLY_DIR.glob("*.md"):
        try:
            if datetime.strptime(f.stem, "%Y%m%d_%H") < cutoff:
                f.unlink()
        except ValueError:
            pass
    print(f"Özet rapor: reports/latest.md ({len(L)} satır)")


if __name__ == "__main__":
    main()
