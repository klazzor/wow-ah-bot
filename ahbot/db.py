import sqlite3

from ahbot.config import DATA_DIR, DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY,
    house_id INTEGER NOT NULL,
    kind TEXT NOT NULL,              -- 'items' | 'commodities'
    last_modified TEXT NOT NULL,
    fetched_ts TEXT NOT NULL,
    n_auctions INTEGER NOT NULL,
    raw_path TEXT,
    UNIQUE (house_id, kind, last_modified)
);
CREATE TABLE IF NOT EXISTS item_summary (
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id),
    item_id INTEGER NOT NULL,
    min_unit INTEGER, p10_unit INTEGER, median_unit INTEGER,
    qty INTEGER NOT NULL, listing_count INTEGER NOT NULL,
    bid_only_count INTEGER NOT NULL,
    PRIMARY KEY (snapshot_id, item_id)
);
CREATE TABLE IF NOT EXISTS auction_seen (
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id),
    auction_id INTEGER NOT NULL,
    item_id INTEGER NOT NULL,
    unit_price INTEGER,
    quantity INTEGER NOT NULL,
    time_left TEXT,
    PRIMARY KEY (snapshot_id, auction_id)
);
CREATE TABLE IF NOT EXISTS items (
    item_id INTEGER PRIMARY KEY,
    name TEXT, quality TEXT, item_class TEXT, item_subclass TEXT,
    sell_price INTEGER, purchase_price INTEGER,
    is_stackable INTEGER, max_count INTEGER,
    source TEXT,          -- hangi namespace'ten geldi; NULL = bulunamadı
    http_status INTEGER,
    fetched_ts TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS recipes (
    recipe_id INTEGER PRIMARY KEY,
    profession TEXT NOT NULL,
    category TEXT,
    name TEXT,
    crafted_item_id INTEGER,       -- enchant'larda NULL (item üretmez)
    crafted_qty_min REAL, crafted_qty_max REAL,
    description TEXT,
    source TEXT NOT NULL,          -- tarifin geldiği namespace
    skill_level INTEGER,           -- öğrenme seviyesi (pattern/formula item'ından); NULL = bilinmiyor
    skill_source TEXT,             -- 'pattern:<item_id>@<namespace>' | 'user' | NULL
    teach_item_id INTEGER,         -- tarifi öğreten item (varsa)
    crafted_in_classic1x INTEGER,  -- 1/0/NULL: ürün Classic Era'da var mı
    forever_flag TEXT,             -- Forever'a özel işaretleri
    fetched_ts TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tsm_realm (       -- TSM public data, forever/eu/realm/pvp-horde (~3 saatte bir)
    item_id INTEGER NOT NULL, name TEXT,
    market_value INTEGER, min_buyout INTEGER, recent INTEGER, historical INTEGER,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (item_id, updated_at)
);
CREATE TABLE IF NOT EXISTS tsm_region (      -- TSM public data, forever/eu/region (günlük; BÖLGE GENELİ)
    item_id INTEGER NOT NULL, name TEXT,
    market_value INTEGER, historical INTEGER, avg_sale_price INTEGER,
    sale_rate REAL, sold_per_day REAL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (item_id, updated_at)
);
CREATE TABLE IF NOT EXISTS fetch_log (
    source TEXT NOT NULL, fetched_ts TEXT NOT NULL, http_status INTEGER, updated_at TEXT, rows INTEGER
);
CREATE TABLE IF NOT EXISTS known_recipes (
    recipe_id INTEGER PRIMARY KEY REFERENCES recipes(recipe_id),
    color TEXT,                    -- ekran görüntüsündeki zorluk rengi (gray/green/yellow/orange)
    learn_source TEXT,             -- Start | Trainer | Recipe
    captured_ts TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS recipe_reagents (
    recipe_id INTEGER NOT NULL REFERENCES recipes(recipe_id),
    item_id INTEGER NOT NULL,
    name TEXT,
    quantity INTEGER NOT NULL,
    PRIMARY KEY (recipe_id, item_id)
);
"""


def connect():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.executescript(SCHEMA)
    return con
