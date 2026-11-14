# data/analyst.sqlite — şema (salt okunur analist kopyası)

Üretici: `ahbot/analyst.py` → `build()`, her saat `scripts/hourly.py` sonunda (rapordan sonra). Önce `analyst.sqlite.tmp`'ye
yazılır, sonra atomik olarak yer değiştirilir. Ana veritabanı `data/ahbot.sqlite`'a YAZMAYIN; bu kopyayı kopyalayıp sorgulayın.
Ev: 113 (EU Forever, PvP Horde). Fiyatlar BAKIR. Zamanlar UTC (aksi yazılmadıkça).
`meta` tablosu: `built_utc`, `item_summary_days`, `house_id`.

| Tablo | İçerik | Kapsam |
|---|---|---|
| `snapshots` | Blizzard snapshot'ları: `id, house_id, kind ('items'/'commodities'), last_modified (RFC1123 metin — sıralamada KULLANMA), fetched_ts (YYYYMMDDTHHMMSSZ, sıralama için bu), n_auctions` | tümü |
| `item_summary` | snapshot × item: `min_unit, p10_unit, median_unit` (adet ağırlıklı), `qty, listing_count, bid_only_count`. PK (item_id, snapshot_id) | son 14 gün |
| `auction_flow` | ardışık iki snapshot arası item başına ilan akışı: `new_count/new_qty`, `gone_count/gone_qty` (kaybolan), `gone_early_count/qty` (önceki time_left LONG/VERY_LONG iken kaybolan → süre dolmadan gitti: satış YA DA iptal, ayırt edilemez), `qty_drop` (commodity kısmi alım), `gone_early_min_price`, `gap_hours` | son 14 gün |
| `items` | item meta: ad, kalite, sınıf, `sell_price` (vendor), `source` (namespace; NULL=bulunamadı) | tümü |
| `recipes`, `recipe_reagents`, `known_recipes` | Tailoring/Enchanting tarifleri, malzemeleri, kullanıcının bildiği tarifler | tümü |
| `tsm_realm` | TSM CSV realm (pvp-horde): `market_value, min_buyout, recent, historical, updated_at`. PK (item_id, updated_at) → zaten zaman serisi, üzerine yazılmaz | tümü |
| `tsm_region` | TSM CSV bölge: `market_value, historical, avg_sale_price, sale_rate, sold_per_day` (BÖLGE GENELİ), `updated_at` | tümü |
| `tsm_realm_history`, `tsm_region_history` | yukarıdaki iki tablonun görünümü (VIEW; aynı veri) | — |
| `fetch_log` | TSM indirme kaydı | tümü |
| `auctionator_history` | Auctionator.lua fiyat geçmişi (aşağıda) | tümü |
| `tsm_local` | TSM masaüstü uygulamasının oyuna indirdiği AppData.lua blokları (aşağıda) | son 14 gün (download_time) |
| `tsm_sales`, `tsm_buys`, `tsm_expired`, `tsm_cancelled`, `tsm_income`, `tsm_expense` | TSM Accounting defteri (aşağıda) | tümü |

## auctionator_history
Kaynak: `WTF\Account\...\SavedVariables\Auctionator.lua` → `AUCTIONATOR_PRICE_DATABASE["PvP"]` (CBOR). SADECE okunur.
Sütunlar: `realm` ('PvP'), `item_key` (Auctionator anahtarı), `item_id`, `day` (YYYY-MM-DD, oyuncunun YEREL günü;
Auctionator gün sayısı 2020-01-01'den), `low`, `high` (o günün gözlenen en düşük/en yüksek fiyatı; Forever'da `low` çoğunlukla boş),
`available` (o gün görülen adet), `imported_ts`. PK (realm, item_key, day); aynı gün yeniden içe aktarılırsa güncellenir.
Sadece oyuncu AH'de tarama yaptığında dolar → kapsama seyrek. Auctionator `price_history_days` = 21.

## tsm_local
Kaynak: `Interface\AddOns\TradeSkillMaster_AppHelper\AppData.lua` (TSM masaüstü uygulaması yazar). SADECE okunur.
Not: `SavedVariables\TradeSkillMaster_AppHelper.lua` fiyat içermiyor (hata raporu/analitik), bu yüzden AppData.lua kullanıldı.
Uzun format: `block, scope, download_time (UTC ISO), item_string, item_id, field, value` (ham tamsayı, base-32 çözülmüş).

| block | scope | field(lar) | ölçek |
|---|---|---|---|
| AUCTIONDB_NON_COMMODITY_DATA | PvP-Horde | minBuyout, numAuctions, marketValueRecent | bakır / adet / bakır (DBMinBuyout, DBRecent) |
| AUCTIONDB_NON_COMMODITY_SCAN_STAT | PvP-Horde | marketValue | bakır (DBMarket) |
| AUCTIONDB_NON_COMMODITY_HISTORICAL | PvP-Horde | historical | bakır (DBHistorical) |
| AUCTIONDB_REGION_STAT | Forever-EU | regionMarketValue | bakır (DBRegionMarketAvg) |
| AUCTIONDB_REGION_HISTORICAL | Forever-EU | regionHistorical | bakır |
| AUCTIONDB_REGION_SALE | Forever-EU | regionSale, regionSoldPerDay, regionSalePercent | bakır / **muhtemelen ×1000** / **muhtemelen ×1000** (doğrulanmadı: Strange Dust regionSoldPerDay=1773948 ↔ CSV sold_per_day 1376.9; regionSalePercent=906 ↔ CSV sale_rate 0.869) |
| AUCTIONDB_COMMODITY_HISTORICAL | Forever-EU | historical | 2026-11-09'da boş |

Adı "NON_COMMODITY" olsa da commodity'ler de var (Strange Dust minBuyout 82 = bizim snapshot min 82).
Her yeni downloadTime yeni satırlar ekler (aynı downloadTime tekrar eklenmez).

## TSM Accounting defteri (`tsm_sales`, `tsm_buys`, `tsm_expired`, `tsm_cancelled`, `tsm_income`, `tsm_expense`) — tümü
Kaynak: `SavedVariables\TradeSkillMaster.lua`, anahtarlar `["r@<realm>@internalData@csv*"]` (SADECE okunur; `ahbot/ledger.py`,
hourly.py'de rapordan ÖNCE, dosya mtime+boyut değişmediyse atlanır). 2026-11-09'da realm anahtarı yalnız `PvP`.
Dosyadaki GERÇEK başlıklar (2026-11-09):

| CSV | Başlık | Tablo |
|---|---|---|
| csvSales, csvBuys | `itemString,stackSize,quantity,price,otherPlayer,player,time,source` | `tsm_sales`, `tsm_buys` |
| csvExpired, csvCancelled | `itemString,stackSize,quantity,player,time` | `tsm_expired`, `tsm_cancelled` |
| csvIncome, csvExpense | `type,amount,otherPlayer,player,time` | `tsm_income`, `tsm_expense` |

Tablo sütunları: `realm, item_string, item_id, stack_size, quantity, unit_price (= TSM price, birim, bakır; AH satışlarında kesinti
ÖNCESİ), total (= quantity × unit_price), other_player ('Merchant', 'Multiple Buyers/Sellers', oyuncu adı), player (karakter),
source ('Auction'|'Vendor'|... — Trade/Mail görülürse aynen yazılır), ts (unix, UTC), time_utc (ISO), dup_n, imported_ts`.
Expired/cancelled: `realm, item_string, item_id, stack_size, quantity, player, ts, time_utc, dup_n, imported_ts`.
Income/expense: `realm, type ('Money Transfer', 'Repair Bill', ...), amount (bakır), other_player, player, ts, time_utc, dup_n, imported_ts`.
`dup_n`: aynı satır dosyada n kez geçiyorsa 0..n-1 (dosya her /reload'da tüm geçmişi içerir → PK ile çift kayıt engellenir).
TSM `stackSize` ≠ `quantity` olabilir (birden çok stack tek kayıtta). Depozito ve ilan süresi CSV'de YOK.
Not: tek mektup (`data/user/ah_mail_log.csv`: 16 Bolt of Linen = 784c → 49c/adet) ile TSM kayıtları birebir eşleşmedi
(16 adet @47c ve 12 adet @49c kayıtları var) — TSM birim fiyatı yuvarlıyor/birleştiriyor olabilir; doğrulanmadı.

## Ana veritabanında olup kopyada OLMAYAN
`auction_seen` (ham ilanlar; ana DB'de son 48 saat tutulur), `import_state`.
