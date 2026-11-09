"""Item meta önbelleği (SQLite `items` tablosu). static-forever-eu 404 verirse
config.ITEM_NAMESPACES sırasıyla fallback dener. Bulunamayanlar da (source=NULL) önbelleğe yazılır.

Kullanım:
  python -m ahbot.items            # son snapshot'lardaki eksik item meta'larını çek
  python -m ahbot.items --levels   # silah/zırh (yeşil+) item'ların eksik item seviyesini tamamla
"""
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from ahbot.api import get
from ahbot.config import ITEM_NAMESPACES, TRUSTED_SELL_SOURCES
from ahbot.db import connect

COLS = ["item_id", "name", "quality", "item_class", "item_subclass", "sell_price", "purchase_price",
        "is_stackable", "max_count", "source", "http_status", "fetched_ts",
        "item_level", "required_level", "inventory_type"]
NEW_COLS = {"item_level": "INTEGER", "required_level": "INTEGER", "inventory_type": "TEXT"}


def migrate(con):
    have = {r[1] for r in con.execute("PRAGMA table_info(items)")}
    for c, t in NEW_COLS.items():
        if c not in have:
            con.execute(f"ALTER TABLE items ADD COLUMN {c} {t}")
    con.commit()


def fetch_one(iid, namespaces=ITEM_NAMESPACES):
    last = None
    for ns in namespaces:
        st, d, _ = get(f"/data/wow/item/{iid}", namespace=ns, timeout=30)
        last = st
        if st == 200 and d:
            return dict(item_id=iid, name=d.get("name"), quality=(d.get("quality") or {}).get("type"),
                        item_class=(d.get("item_class") or {}).get("name"),
                        item_subclass=(d.get("item_subclass") or {}).get("name"),
                        sell_price=d.get("sell_price"), purchase_price=d.get("purchase_price"),
                        is_stackable=int(bool(d.get("is_stackable"))), max_count=d.get("max_count"),
                        source=ns, http_status=st, item_level=d.get("level"),
                        required_level=d.get("required_level"),
                        inventory_type=(d.get("inventory_type") or {}).get("type"))
    return dict(item_id=iid, source=None, http_status=last)


def _upsert(con, rows, now):
    sql = f"INSERT OR REPLACE INTO items ({','.join(COLS)}) VALUES ({','.join('?' * len(COLS))})"
    con.executemany(sql, [tuple({**r, "fetched_ts": now}.get(c) for c in COLS) for r in rows])
    con.commit()


def _run(con, ids, namespaces=ITEM_NAMESPACES, workers=8, keep_existing_on_fail=False):
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    done, batch, failed = 0, [], 0
    with ThreadPoolExecutor(workers) as ex:
        futs = [ex.submit(fetch_one, i, namespaces) for i in ids]
        for f in as_completed(futs):
            r = f.result()
            done += 1
            if r["source"] is None and (keep_existing_on_fail or r["http_status"] != 404):
                # Geçici hata (429/5xx) ya da mevcut iyi kaydı ezme riski → yazma
                failed += 1
                continue
            batch.append(r)
            if len(batch) >= 200:
                _upsert(con, batch, now)
                batch = []
                print(f"  {done}/{len(ids)}")
    if batch:
        _upsert(con, batch, now)
    if failed:
        print(f"  {failed} istek başarısız (404 dışı hata ya da mevcut kayıt korundu) — yazılmadı")


def fill():
    con = connect()
    migrate(con)
    q = """SELECT DISTINCT s.item_id FROM item_summary s
           WHERE s.snapshot_id IN (SELECT MAX(id) FROM snapshots GROUP BY kind)
             AND s.item_id NOT IN (SELECT item_id FROM items)"""
    ids = [r[0] for r in con.execute(q)]
    print(f"Meta eksik item: {len(ids)}")
    _run(con, ids)
    print("Kaynak dağılımı:")
    for src, n in con.execute("SELECT COALESCE(source,'BULUNAMADI'), COUNT(*) FROM items GROUP BY 1 ORDER BY 2 DESC"):
        print(f"  {src}: {n}")


def fill_levels():
    """Eski kayıtlarda item_level yok; DE adayları için (silah/zırh, yeşil+) yeniden çek."""
    con = connect()
    migrate(con)
    ph = ",".join("?" * len(TRUSTED_SELL_SOURCES))
    ids = [r[0] for r in con.execute(
        f"""SELECT item_id FROM items WHERE item_level IS NULL AND source IN ({ph})
            AND item_class IN ('Weapon','Armor') AND quality IN ('UNCOMMON','RARE','EPIC')""",
        TRUSTED_SELL_SOURCES)]
    print(f"Seviyesi eksik silah/zırh: {len(ids)}")
    _run(con, ids, namespaces=TRUSTED_SELL_SOURCES, keep_existing_on_fail=True)
    n = con.execute("SELECT COUNT(*) FROM items WHERE item_level IS NOT NULL").fetchone()[0]
    print(f"item_level dolu kayıt: {n}")


def ensure(ids):
    """Verilen item'lardan meta'sı olmayanları çek (envanter, tarif ürünleri/malzemeleri için)."""
    con = connect()
    migrate(con)
    have = {r[0] for r in con.execute("SELECT item_id FROM items")}
    missing = [i for i in set(ids) if i not in have]
    if missing:
        print(f"Meta eksik {len(missing)} item çekiliyor...")
        _run(con, missing, workers=4)
    return len(missing)


def retry_missing():
    """source=NULL olan (bulunamayan ya da hata yüzünden silinmiş) item'ları yeniden dene."""
    con = connect()
    migrate(con)
    ids = [r[0] for r in con.execute("SELECT item_id FROM items WHERE source IS NULL")]
    print(f"Yeniden denenecek: {len(ids)}")
    _run(con, ids, workers=4)
    print("Kaynak dağılımı:")
    for src, n in con.execute("SELECT COALESCE(source,'BULUNAMADI'), COUNT(*) FROM items GROUP BY 1 ORDER BY 2 DESC"):
        print(f"  {src}: {n}")


if __name__ == "__main__":
    if "--levels" in sys.argv:
        fill_levels()
    elif "--retry" in sys.argv:
        retry_missing()
    else:
        fill()
