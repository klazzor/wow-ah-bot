"""Analist kopyası, ilan akışı özeti ve ham ilan saklama kuralı.

- auction_flow: ardışık iki snapshot (aynı tür) arasında item başına yeni / kaybolan ilanlar. Satış tahmini için
  ham `auction_seen` silinmeden ÖNCE hesaplanır. "erken kaybolan" = önceki snapshot'ta time_left LONG/VERY_LONG
  olup kaybolan → süre dolmadan gitti (satıldı ya da iptal edildi; ikisi ayırt edilemez).
  qty_drop = aynı ilanın adedi azaldı (commodity kısmi alımı).
- prune_auction_seen: AUCTION_SEEN_KEEP_HOURS'tan eski `auction_seen` satırlarını siler (akış özeti hesaplandıktan sonra).
  Ham gzip JSON'lar RAW_KEEP_DAYS gün kalır; gerekirse oradan yeniden üretilebilir.
- build(): data/analyst.sqlite (salt okunur sorgu için küçük kopya). Önce geçici dosya, sonra atomik yer değiştirme.

Kullanım: python -m ahbot.analyst            # akış + saklama + kopya
"""
import os
import sqlite3
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ahbot.config import DATA_DIR, DB_PATH, HOUSE_ID

AUCTION_SEEN_KEEP_HOURS = 48
ANALYST_DAYS = 14
ANALYST_PATH = DATA_DIR / "analyst.sqlite"

FLOW_SCHEMA = """
CREATE TABLE IF NOT EXISTS auction_flow (
    snapshot_id INTEGER NOT NULL,        -- yeni snapshot
    prev_snapshot_id INTEGER NOT NULL,   -- karşılaştırılan önceki snapshot (aynı ev, aynı tür)
    gap_hours REAL,                      -- iki snapshot arası saat (fetched_ts)
    item_id INTEGER NOT NULL,
    new_count INTEGER, new_qty INTEGER,          -- yeni görülen ilanlar
    gone_count INTEGER, gone_qty INTEGER,        -- kaybolan ilanlar (toplam)
    gone_early_count INTEGER, gone_early_qty INTEGER,  -- önceki time_left LONG/VERY_LONG iken kaybolan
    qty_drop INTEGER,                            -- duran ilanlarda adet azalması (kısmi alım)
    gone_early_min_price INTEGER,                -- erken kaybolanların en düşük birim fiyatı
    PRIMARY KEY (snapshot_id, item_id)
) WITHOUT ROWID;
"""

ANALYST_TABLES = ["items", "recipes", "recipe_reagents", "known_recipes", "tsm_realm", "tsm_region", "snapshots",
                  "fetch_log", "auction_flow", "auctionator_history", "tsm_local",
                  "tsm_sales", "tsm_buys", "tsm_expired", "tsm_cancelled", "tsm_income", "tsm_expense"]
# Kişisel (SavedVariables / oyun dosyası kaynaklı) tablolar: AÇIK depoya / GitHub Actions kopyasına ASLA girmez.
PRIVATE_TABLES = {"known_recipes", "auctionator_history", "tsm_local", "import_state",
                  "tsm_sales", "tsm_buys", "tsm_expired", "tsm_cancelled", "tsm_income", "tsm_expense"}


def _ts(s):
    return datetime.strptime(s, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)


def compute_flow(con):
    """auction_seen'i hâlâ duran ardışık snapshot çiftleri için eksik auction_flow kayıtlarını üretir."""
    con.executescript(FLOW_SCHEMA)
    n_pairs = 0
    for kind in ("items", "commodities"):
        snaps = con.execute("SELECT id, fetched_ts FROM snapshots WHERE house_id=? AND kind=? ORDER BY fetched_ts",
                            (HOUSE_ID, kind)).fetchall()
        have_raw = {r[0] for r in con.execute("SELECT DISTINCT snapshot_id FROM auction_seen")}
        done = {r[0] for r in con.execute("SELECT DISTINCT snapshot_id FROM auction_flow")}
        for (pid, pts), (sid, sts) in zip(snaps, snaps[1:]):
            if sid in done or sid not in have_raw or pid not in have_raw:
                continue
            gap = round((_ts(sts) - _ts(pts)).total_seconds() / 3600, 2)
            q = "SELECT auction_id, item_id, unit_price, quantity, time_left FROM auction_seen WHERE snapshot_id=?"
            prev = {r[0]: r[1:] for r in con.execute(q, (pid,))}
            cur = {r[0]: r[1:] for r in con.execute(q, (sid,))}
            # item_id → [new_c, new_q, gone_c, gone_q, early_c, early_q, qty_drop, early_min]
            agg = defaultdict(lambda: [0, 0, 0, 0, 0, 0, 0, None])
            for aid, (iid, up, qty, tl) in cur.items():
                p = prev.get(aid)
                if p is None:
                    a = agg[iid]; a[0] += 1; a[1] += qty
                elif qty < p[2]:
                    agg[iid][6] += p[2] - qty
            for aid, (iid, up, qty, tl) in prev.items():
                if aid in cur:
                    continue
                a = agg[iid]; a[2] += 1; a[3] += qty
                if tl in ("LONG", "VERY_LONG"):
                    a[4] += 1; a[5] += qty
                    if up is not None and (a[7] is None or up < a[7]):
                        a[7] = up
            con.executemany("INSERT OR IGNORE INTO auction_flow VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                            [(sid, pid, gap, iid, *a) for iid, a in agg.items()])
            n_pairs += 1
    con.commit()
    if n_pairs:
        print(f"auction_flow: {n_pairs} snapshot çifti işlendi")
    return n_pairs


def prune_auction_seen(con, keep_hours=AUCTION_SEEN_KEEP_HOURS):
    """keep_hours'tan eski snapshot'ların ham ilanlarını sil. Her türün son 2 snapshot'ı her durumda kalır,
    akışı hesaplanmamış snapshot (sonraki çiftin öncülü) silinmez."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=keep_hours)).strftime("%Y%m%dT%H%M%SZ")
    keep = {r[0] for r in con.execute(
        "SELECT id FROM (SELECT id, ROW_NUMBER() OVER (PARTITION BY kind ORDER BY fetched_ts DESC) rn FROM snapshots) WHERE rn<=2")}
    flowed = {r[0] for r in con.execute("SELECT DISTINCT prev_snapshot_id FROM auction_flow")}
    old = [r[0] for r in con.execute("SELECT id FROM snapshots WHERE fetched_ts < ?", (cutoff,))
           if r[0] not in keep and r[0] in flowed]
    n = 0
    for sid in old:
        n += con.execute("DELETE FROM auction_seen WHERE snapshot_id=?", (sid,)).rowcount
    con.commit()
    if n:
        print(f"auction_seen: {len(old)} eski snapshot'ın {n} ham ilanı silindi (> {keep_hours} sa)")
    return n


def build(days=ANALYST_DAYS, dest=ANALYST_PATH, public=False):
    """data/analyst.sqlite üret (geçici dosya → atomik yer değiştirme). Döner: bayt boyutu.
    public=True: kişisel tablolar (PRIVATE_TABLES) konmaz — açık depo / GitHub Actions için."""
    dest = Path(dest)
    tmp = dest.with_suffix(".sqlite.tmp")
    tmp.unlink(missing_ok=True)
    out = sqlite3.connect(tmp)
    out.execute("ATTACH DATABASE ? AS src", (str(DB_PATH),))
    have = {r[0]: r[1] for r in out.execute("SELECT name, sql FROM src.sqlite_master WHERE type='table'")}
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y%m%dT%H%M%SZ")
    cutoff_iso = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    windowed = {   # büyüyen tablolar: sadece son `days` gün
        "auction_flow": ("WHERE snapshot_id IN (SELECT id FROM src.snapshots WHERE fetched_ts >= ?)", cutoff),
        "tsm_local": ("WHERE download_time >= ?", cutoff_iso),
    }
    for t in ANALYST_TABLES:
        if public and t in PRIVATE_TABLES:
            continue
        if t in have:
            out.execute(have[t])
            where, arg = windowed.get(t, ("", None))
            out.execute(f"INSERT INTO main.{t} SELECT * FROM src.{t} {where}", (arg,) if where else ())
    # item_summary: son `days` gün (fetched_ts'e göre), PK kümelenmiş (WITHOUT ROWID) → ayrı indeks yok
    out.execute("""CREATE TABLE item_summary (
        snapshot_id INTEGER NOT NULL, item_id INTEGER NOT NULL,
        min_unit INTEGER, p10_unit INTEGER, median_unit INTEGER,
        qty INTEGER NOT NULL, listing_count INTEGER NOT NULL, bid_only_count INTEGER NOT NULL,
        PRIMARY KEY (item_id, snapshot_id)) WITHOUT ROWID""")
    out.execute("""INSERT INTO item_summary SELECT i.* FROM src.item_summary i
                   JOIN src.snapshots s ON s.id=i.snapshot_id WHERE s.fetched_ts >= ?""", (cutoff,))
    # Adlandırma kolaylığı: TSM CSV tabloları zaten zaman serisi (PK item_id+updated_at, üzerine yazılmaz)
    out.execute("CREATE VIEW tsm_realm_history AS SELECT * FROM tsm_realm")
    out.execute("CREATE VIEW tsm_region_history AS SELECT * FROM tsm_region")
    out.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    out.executemany("INSERT INTO meta VALUES (?,?)", [
        ("built_utc", datetime.now(timezone.utc).isoformat(timespec="seconds")),
        ("item_summary_days", str(days)), ("house_id", str(HOUSE_ID)), ("schema_doc", "data/analyst_schema.md"),
        ("public", "1" if public else "0")])
    out.commit()
    out.execute("DETACH DATABASE src")
    out.execute("VACUUM")
    out.close()
    for attempt in range(5):
        try:
            os.replace(tmp, dest)
            break
        except PermissionError:      # okuyan biri dosyayı açık tutuyorsa (Windows) kısa bekle
            time.sleep(2)
    else:
        raise RuntimeError(f"{dest} yer değiştirilemedi (dosya açık?); yeni kopya {tmp} olarak kaldı")
    size = dest.stat().st_size
    print(f"analyst.sqlite: {size / 1e6:.1f} MB")
    return size


def run(public=False):
    from ahbot.db import connect
    con = connect()
    compute_flow(con)
    prune_auction_seen(con)
    con.close()
    return build(public=public)


if __name__ == "__main__":
    run()
