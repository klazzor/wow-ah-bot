"""TSM Accounting (alım-satım defteri) içe aktarımı. TradeSkillMaster.lua SavedVariables'ı SADECE OKUR, asla yazmaz.

Kaynak anahtarlar: ["r@<realm>@internalData@csvSales|csvBuys|csvExpired|csvCancelled|csvIncome|csvExpense"] = "<CSV>"
(Lua dizesi, satırlar "\\n" ile ayrılmış, ilk satır başlık). Gerçek başlıklar (2026-11-09, dosyadan okundu):
  csvSales / csvBuys      : itemString,stackSize,quantity,price,otherPlayer,player,time,source
  csvExpired / csvCancelled: itemString,stackSize,quantity,player,time
  csvIncome / csvExpense  : type,amount,otherPlayer,player,time
price = BİRİM fiyat (bakır, TSM'in kaydettiği; AH satışlarında kesinti ÖNCESİ). time = unix epoch (UTC).
Sütunlar konumla değil başlık adıyla eşlenir. Dosya her /reload'da tüm geçmişi tekrar içerir → çift kayıt
PK ile engellenir (aynı satır aynı dosyada n kez geçiyorsa dup_n = 0..n-1). Dosya değişmediyse (mtime+boyut) atlanır.

Kullanım: python -m ahbot.ledger [--force]
"""
import re
import sys
from collections import Counter
from datetime import datetime, timezone

from ahbot.db import connect
from ahbot.history import SCHEMA as HISTORY_SCHEMA, _changed, _iid, _lua_string_bytes, _mark
from ahbot.savedvars import TSM_SV

TRADE_COLS = """realm TEXT NOT NULL, item_string TEXT NOT NULL, item_id INTEGER, stack_size INTEGER, quantity INTEGER,
    unit_price INTEGER, total INTEGER, other_player TEXT, player TEXT, source TEXT, ts INTEGER NOT NULL, time_utc TEXT,
    dup_n INTEGER NOT NULL, imported_ts TEXT,
    PRIMARY KEY (realm, item_string, stack_size, quantity, unit_price, other_player, player, source, ts, dup_n)"""
LIST_COLS = """realm TEXT NOT NULL, item_string TEXT NOT NULL, item_id INTEGER, stack_size INTEGER, quantity INTEGER,
    player TEXT, ts INTEGER NOT NULL, time_utc TEXT, dup_n INTEGER NOT NULL, imported_ts TEXT,
    PRIMARY KEY (realm, item_string, stack_size, quantity, player, ts, dup_n)"""
MONEY_COLS = """realm TEXT NOT NULL, type TEXT NOT NULL, amount INTEGER, other_player TEXT, player TEXT,
    ts INTEGER NOT NULL, time_utc TEXT, dup_n INTEGER NOT NULL, imported_ts TEXT,
    PRIMARY KEY (realm, type, amount, other_player, player, ts, dup_n)"""

# csv anahtarı → (tablo, sütun tipi)
SOURCES = {"csvSales": ("tsm_sales", "trade"), "csvBuys": ("tsm_buys", "trade"),
           "csvExpired": ("tsm_expired", "list"), "csvCancelled": ("tsm_cancelled", "list"),
           "csvIncome": ("tsm_income", "money"), "csvExpense": ("tsm_expense", "money")}
SCHEMA = "\n".join(f"CREATE TABLE IF NOT EXISTS {t} ({dict(trade=TRADE_COLS, list=LIST_COLS, money=MONEY_COLS)[k]}) WITHOUT ROWID;"
                   for t, k in SOURCES.values())
LEDGER_TABLES = [t for t, _ in SOURCES.values()]

_KEY = re.compile(rb'\["r@([^"@]+)@internalData@(csv\w+)"\]\s*=\s*"')


def read_csvs(path=TSM_SV):
    """[(realm, csv_adı, başlık[list], satırlar[list[list]])] — dosya SADECE okunur."""
    buf = path.read_bytes()
    out = []
    for m in _KEY.finditer(buf):
        realm, name = m.group(1).decode("utf-8", "replace"), m.group(2).decode()
        if name not in SOURCES:
            continue
        text = _lua_string_bytes(buf, m.end() - 1).decode("utf-8", "replace")
        lines = [l for l in text.split("\n") if l.strip()]
        if not lines:
            continue
        out.append((realm, name, lines[0].split(","), [l.split(",") for l in lines[1:]]))
    return out


def _utc(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _rows(realm, name, header, data, now):
    kind = SOURCES[name][1]
    seen = Counter()
    out = []
    for f in data:
        if len(f) != len(header) or any(v == "" for v in f):   # WITHOUT ROWID PK'de NULL olamaz → eksik satırı atla
            continue
        r = dict(zip(header, f))
        ts = _int(r.get("time"))
        if ts is None:
            continue
        if kind == "trade":
            q, p = _int(r.get("quantity")), _int(r.get("price"))
            key = (realm, r["itemString"], _int(r.get("stackSize")), q, p, r.get("otherPlayer"), r.get("player"),
                   r.get("source"), ts)
            n = seen[key]
            seen[key] += 1
            out.append((realm, r["itemString"], _iid(r["itemString"]), key[2], q, p,
                        q * p if q is not None and p is not None else None, key[5], key[6], key[7], ts, _utc(ts), n, now))
        elif kind == "list":
            key = (realm, r["itemString"], _int(r.get("stackSize")), _int(r.get("quantity")), r.get("player"), ts)
            n = seen[key]
            seen[key] += 1
            out.append((realm, r["itemString"], _iid(r["itemString"]), key[2], key[3], key[4], ts, _utc(ts), n, now))
        else:
            key = (realm, r.get("type"), _int(r.get("amount")), r.get("otherPlayer"), r.get("player"), ts)
            n = seen[key]
            seen[key] += 1
            out.append((*key, _utc(ts), n, now))
    return out


def import_ledger(con, force=False, path=TSM_SV):
    con.executescript(HISTORY_SCHEMA)   # import_state
    con.executescript(SCHEMA)
    if not path.exists():
        print("TradeSkillMaster.lua yok, defter atlandı")
        return 0
    changed, st = _changed(con, "tsm_ledger", path, force)
    if not changed:
        print("tsm_ledger: dosya değişmedi, atlandı")
        return 0
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    total_new = 0
    for realm, name, header, data in read_csvs(path):
        table = SOURCES[name][0]
        rows = _rows(realm, name, header, data, now)
        if not rows:
            continue
        before = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        con.executemany(f"INSERT OR IGNORE INTO {table} VALUES ({','.join('?' * len(rows[0]))})", rows)
        new = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] - before
        total_new += new
        print(f"tsm_ledger: {realm} {name}: dosyada {len(rows)}, yeni {new}")
    _mark(con, "tsm_ledger", path, st, total_new)
    con.commit()
    return total_new


def run(force=False):
    con = connect()
    import_ledger(con, force)
    return con


if __name__ == "__main__":
    run(force="--force" in sys.argv).close()
