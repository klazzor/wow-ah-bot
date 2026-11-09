"""Piyasa değeri ve satış hızı yardımcıları."""
from collections import defaultdict

from ahbot.config import HOUSE_ID, P_UNSOLD_DEFAULT

SLOW_SOLD_PER_DAY = 5.0   # TSM bölge soldPerDay < bu → "yavaş satış" işareti (kullanıcı kuralı, 2026-11-09; tüm raporlar)


def latest_snapshot_ids(con):
    return [r[0] for r in con.execute(
        "SELECT MAX(id) FROM snapshots WHERE house_id=? GROUP BY kind", (HOUSE_ID,))]


def market_values(con, lo=0.15, hi=0.30):
    """Bizim piyasa değerimiz: adet ağırlıklı fiyat dağılımının alt %15–30 diliminin medyanı.
    Döner: {item_id: (market_value, toplam_adet, ilan_sayısı, min)}"""
    sids = latest_snapshot_ids(con)
    per = defaultdict(list)
    q = f"SELECT item_id, unit_price, quantity FROM auction_seen WHERE snapshot_id IN ({','.join('?' * len(sids))}) AND unit_price IS NOT NULL"
    for iid, p, n in con.execute(q, sids):
        per[iid].append((p, n))
    out = {}
    for iid, pairs in per.items():
        pairs.sort()
        total = sum(n for _, n in pairs)
        a, b = lo * total, hi * total
        mid = (a + b) / 2
        acc, mv = 0, pairs[0][0]
        for p, n in pairs:
            acc += n
            if acc >= mid:
                mv = p
                break
        out[iid] = (mv, total, len(pairs), pairs[0][0])
    return out


def p_unsold(tsm_row):
    """TSM bölge saleRate varsa 1 − saleRate, yoksa config varsayılanı. (kaynak, değer) döner."""
    if tsm_row and tsm_row.get("g_rate") is not None:
        return "tsm", max(0.0, min(1.0, 1 - tsm_row["g_rate"]))
    return "varsayılan", P_UNSOLD_DEFAULT


def velocity_flag(tsm_row):
    spd = (tsm_row or {}).get("g_spd")
    if spd is None:
        return "satış hızı bilinmiyor"
    if spd < SLOW_SOLD_PER_DAY:
        return f"⚠️ yavaş satış ({spd:g}/gün, bölge)"
    return ""
