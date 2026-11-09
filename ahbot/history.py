"""Oyun içi eklentilerin yerel fiyat geçmişini içe aktarır. WoW klasöründeki dosyaları SADECE OKUR, asla yazmaz.

1. Auctionator: SavedVariables/Auctionator.lua → AUCTIONATOR_PRICE_DATABASE["<realm>"]
   (CBOR ile serileştirilmiş Lua dizesi; item anahtarı → {m: güncel min, h: {gün: yüksek}, l: {gün: düşük}, a: {gün: adet}})
   Gün = 2020-01-01'den (yerel saat) itibaren gün sayısı (Auctionator SCAN_DAY_0). → tablo `auctionator_history`
2. TSM: Interface/AddOns/TradeSkillMaster_AppHelper/AppData.lua (TSM masaüstü uygulamasının indirdiği fiyat blokları;
   SavedVariables/TradeSkillMaster_AppHelper.lua sadece hata raporu/analitik içeriyor, fiyat yok — 2026-11-09).
   Bloklar: AUCTIONDB_* (PvP-Horde realm / Forever-EU bölge), downloadTime ile. Sayılar TSM base-32 (0-9A-V). → `tsm_local`
Dosya değişmediyse (mtime+boyut aynı) atlanır; aynı (gün/downloadTime) kaydı tekrar eklenmez.

Kullanım: python -m ahbot.history [--force]
"""
import re
import sys
from datetime import date, datetime, timedelta, timezone

from ahbot.db import connect
from ahbot.savedvars import TSM_SV, WOW_DIR

AUCTIONATOR_SV = TSM_SV.parent / "Auctionator.lua"
TSM_APPDATA = WOW_DIR / r"Interface\AddOns\TradeSkillMaster_AppHelper\AppData.lua"
AUCTIONATOR_REALM = "PvP"          # Auctionator realm anahtarı (ev 113 = PvP Horde)
TSM_SCOPES = ("PvP-Horde", "Forever-EU")
SCAN_DAY_0 = date(2020, 1, 1)

SCHEMA = """
CREATE TABLE IF NOT EXISTS auctionator_history (
    realm TEXT NOT NULL,            -- Auctionator realm anahtarı ('PvP')
    item_key TEXT NOT NULL,         -- Auctionator anahtarı ('2589' ya da 'g:...' gibi)
    item_id INTEGER,                -- anahtardaki ilk sayı
    day TEXT NOT NULL,              -- YYYY-MM-DD (oyuncunun yerel günü)
    low INTEGER, high INTEGER,      -- o günün en düşük / en yüksek gözlenen fiyatı (bakır)
    available INTEGER,              -- o gün görülen adet
    source TEXT NOT NULL DEFAULT 'auctionator',
    imported_ts TEXT NOT NULL,
    PRIMARY KEY (realm, item_key, day)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS tsm_local (
    block TEXT NOT NULL,            -- AUCTIONDB_NON_COMMODITY_DATA, AUCTIONDB_REGION_SALE ...
    scope TEXT NOT NULL,            -- 'PvP-Horde' | 'Forever-EU'
    download_time TEXT NOT NULL,    -- blok downloadTime (UTC ISO)
    item_string TEXT NOT NULL,
    item_id INTEGER,
    field TEXT NOT NULL,            -- minBuyout, marketValue, regionSoldPerDay ...
    value INTEGER,                  -- ham tamsayı (base-32 çözülmüş); ölçek için data/analyst_schema.md
    PRIMARY KEY (block, scope, download_time, item_string, field)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS import_state (
    source TEXT PRIMARY KEY, path TEXT, mtime REAL, size INTEGER, imported_ts TEXT, rows INTEGER
);
"""


# ---------- Lua dizesi + CBOR ----------
def _lua_string_bytes(buf, start):
    """buf[start] = açılış tırnağı. Lua kaçışlarını çözüp ham baytları döndürür."""
    out = bytearray()
    i = start + 1
    esc = {ord("n"): 10, ord("r"): 13, ord("t"): 9, ord("a"): 7, ord("b"): 8, ord("f"): 12, ord("v"): 11,
           ord("\\"): 92, ord('"'): 34, ord("'"): 39, 10: 10, 13: 13}
    n = len(buf)
    while i < n:
        c = buf[i]
        if c == 34:
            return bytes(out)
        if c != 92:
            out.append(c)
            i += 1
            continue
        d = buf[i + 1]
        if 48 <= d <= 57:
            j = i + 1
            while j < i + 4 and j < n and 48 <= buf[j] <= 57:
                j += 1
            out.append(int(buf[i + 1:j]))
            i = j
        elif d in esc:
            out.append(esc[d])
            i += 2
            if d == 13 and i < n and buf[i] == 10:   # "\<CR><LF>"
                i += 1
        else:
            raise ValueError(f"bilinmeyen Lua kaçışı \\{chr(d)} @ {i}")
    raise ValueError("Lua dizesi kapanmadı")


def _cbor(b, i=0):
    """Minimal CBOR çözücü (int, bytes/text, array, map, float, true/false/null). Döner (değer, sonraki indeks)."""
    ib = b[i]
    mt, ai = ib >> 5, ib & 31
    i += 1
    if mt == 7:
        if ai == 20: return False, i
        if ai == 21: return True, i
        if ai in (22, 23): return None, i
        import struct
        if ai == 25:
            return struct.unpack(">e", b[i:i + 2])[0], i + 2
        if ai == 26:
            return struct.unpack(">f", b[i:i + 4])[0], i + 4
        if ai == 27:
            return struct.unpack(">d", b[i:i + 8])[0], i + 8
        raise ValueError(f"CBOR simple {ai}")
    if ai < 24:
        arg = ai
    elif ai in (24, 25, 26, 27):
        k = 1 << (ai - 24)
        arg = int.from_bytes(b[i:i + k], "big")
        i += k
    else:
        raise ValueError(f"CBOR belirsiz uzunluk desteklenmiyor (ai={ai})")
    if mt == 0: return arg, i
    if mt == 1: return -1 - arg, i
    if mt in (2, 3):
        return b[i:i + arg].decode("utf-8", "replace"), i + arg
    if mt == 4:
        arr = []
        for _ in range(arg):
            v, i = _cbor(b, i)
            arr.append(v)
        return arr, i
    if mt == 5:
        d = {}
        for _ in range(arg):
            k, i = _cbor(b, i)
            v, i = _cbor(b, i)
            d[k] = v
        return d, i
    if mt == 6:      # etiket: içeriği döndür
        return _cbor(b, i)
    raise ValueError(f"CBOR major {mt}")


def read_auctionator(path=AUCTIONATOR_SV, realm=AUCTIONATOR_REALM):
    """{item_key: {...}} — dosya SADECE okunur."""
    buf = path.read_bytes()
    i = buf.find(b"AUCTIONATOR_PRICE_DATABASE = {")
    if i < 0:
        raise RuntimeError("AUCTIONATOR_PRICE_DATABASE bulunamadı")
    m = re.compile(rb'\["' + re.escape(realm.encode()) + rb'"\] = "').search(buf, i)
    if not m:
        raise RuntimeError(f"Auctionator realm '{realm}' bulunamadı")
    raw = _lua_string_bytes(buf, m.end() - 1)
    data, end = _cbor(raw)
    if end != len(raw):
        print(f"Uyarı: CBOR sonu {end} != {len(raw)}")
    return data


def _day(n):
    return (SCAN_DAY_0 + timedelta(days=int(n))).isoformat()


def _iid(key):
    m = re.search(r"\d+", str(key))
    return int(m.group(0)) if m else None


# ---------- TSM AppData ----------
_B32 = {c: n for n, c in enumerate("0123456789ABCDEFGHIJKLMNOPQRSTUV")}


def _b32(s):
    s = s.strip()
    if not s:
        return None
    if s.startswith('"'):
        s = s.strip('"')
    neg = s.startswith("-")
    v = 0
    for ch in s.lstrip("-"):
        v = v * 32 + _B32[ch]
    return -v if neg else v


_BLOCK = re.compile(r'LoadData\("(\w+)","([^"]+)",\[\[return \{downloadTime=(\d+),fields=\{([^}]*)\},data=\{(.*)\}\}\]\]\)')
_ROW = re.compile(r'\{([^{}]*)\}')


def read_tsm_appdata(path=TSM_APPDATA, scopes=TSM_SCOPES):
    """[(block, scope, download_time_iso, fields, [[item_string, v1, ...], ...])] — dosya SADECE okunur."""
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = _BLOCK.search(line)
        if not m or not m.group(1).startswith("AUCTIONDB") or m.group(2) not in scopes:
            continue
        fields = [f.strip('"') for f in m.group(4).split(",")]
        dl = datetime.fromtimestamp(int(m.group(3)), timezone.utc).isoformat(timespec="seconds")
        rows = []
        for r in _ROW.finditer(m.group(5)):
            parts = r.group(1).split(",")
            rows.append([parts[0].strip('"')] + [_b32(p) for p in parts[1:]])
        out.append((m.group(1), m.group(2), dl, fields, rows))
    return out


# ---------- içe aktarma ----------
def _changed(con, source, path, force):
    st = path.stat()
    r = con.execute("SELECT mtime, size FROM import_state WHERE source=?", (source,)).fetchone()
    return force or r is None or r[0] != st.st_mtime or r[1] != st.st_size, st


def _mark(con, source, path, st, rows):
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    con.execute("INSERT OR REPLACE INTO import_state VALUES (?,?,?,?,?,?)",
                (source, str(path), st.st_mtime, st.st_size, now, rows))


def import_auctionator(con, force=False):
    if not AUCTIONATOR_SV.exists():
        print("Auctionator.lua yok, atlandı")
        return 0
    changed, st = _changed(con, "auctionator", AUCTIONATOR_SV, force)
    if not changed:
        print("auctionator: dosya değişmedi, atlandı")
        return 0
    db = read_auctionator()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = []
    for key, e in db.items():
        if not isinstance(e, dict):
            continue
        h, l, a = e.get("h") or {}, e.get("l") or {}, e.get("a") or {}
        for d in set(h) | set(l) | set(a):
            rows.append((AUCTIONATOR_REALM, str(key), _iid(key), _day(d), l.get(d), h.get(d), a.get(d), "auctionator", now))
    # Aynı (realm, item, gün) varsa günceller (o günün değeri /reload'lar arasında değişebilir) → çift kayıt olmaz
    con.executemany("INSERT OR REPLACE INTO auctionator_history VALUES (?,?,?,?,?,?,?,?,?)", rows)
    _mark(con, "auctionator", AUCTIONATOR_SV, st, len(rows))
    con.commit()
    print(f"auctionator: {len(db)} item, {len(rows)} gün kaydı yazıldı")
    return len(rows)


def import_tsm_local(con, force=False):
    if not TSM_APPDATA.exists():
        print("TSM AppData.lua yok, atlandı")
        return 0
    changed, st = _changed(con, "tsm_appdata", TSM_APPDATA, force)
    if not changed:
        print("tsm_local: dosya değişmedi, atlandı")
        return 0
    total = 0
    for block, scope, dl, fields, data in read_tsm_appdata():
        if con.execute("SELECT 1 FROM tsm_local WHERE block=? AND scope=? AND download_time=? LIMIT 1",
                       (block, scope, dl)).fetchone():
            continue   # bu downloadTime zaten var
        rows = []
        for r in data:
            for f, v in zip(fields[1:], r[1:]):
                rows.append((block, scope, dl, r[0], _iid(r[0]), f, v))
        con.executemany("INSERT OR IGNORE INTO tsm_local VALUES (?,?,?,?,?,?,?)", rows)
        total += len(rows)
        print(f"tsm_local: {block} {scope} {dl}: {len(data)} item, {len(rows)} satır")
    _mark(con, "tsm_appdata", TSM_APPDATA, st, total)
    con.commit()
    if not total:
        print("tsm_local: yeni downloadTime yok")
    return total


def run(force=False):
    con = connect()
    con.executescript(SCHEMA)
    import_auctionator(con, force)
    import_tsm_local(con, force)
    return con


if __name__ == "__main__":
    run(force="--force" in sys.argv)
