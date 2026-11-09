"""Proje ayarları. Masraf katsayıları Classic varsayımı — gerçek satış mektuplarıyla doğrula."""
from pathlib import Path

RULESET_ID = 1          # PvP (doğrulandı 2026-11-09)
HOUSE_ID = 113          # PvP Horde AH (oyundaki Mageweave/Copper Ore fiyatıyla doğrulandı)
FACTION = "Horde"
PROFESSIONS = ["Tailoring", "Enchanting"]
TAILORING_SKILL = 100        # 2026-11-09
ENCHANTING_SKILL = 110       # 2026-11-09
CAPITAL_COPPER = 2 * 10000   # mevcut altın: 2g

# Disenchant stratejisi: DE sonucu rastgele → beklenen değere güvenlik payı koy
DE_MIN_PROFIT_COPPER = 50    # ilan başına en az bu kadar beklenen kâr
DE_MIN_MARGIN = 0.20         # beklenen kâr / alış en az %20
P_UNSOLD_DEFAULT = 0.30      # satılmama olasılığı (satış hızı verisi birikene kadar yer tutucu)
DEFAULT_LIST_HOURS = 24

# Kanıtlar: data/user/ah_mail_log.csv
AH_CUT_FACTION = 0.05        # DOĞRULANDI (2026-11-09): Bolt of Linen Cloth ×16, 784c satış → 39c kesinti (39,2c aşağı yuvarlanır)
AH_CUT_FACTION_STATUS = "doğrulandı"
AH_CUT_NEUTRAL = 0.15        # doğrulanmadı (Classic varsayımı)
# Depozito = vendor sell fiyatı × adet × katsayı; satılırsa iade, süre dolarsa/iptalde yanar.
# Kanıt (2026-11-09): Bolt of Linen Cloth ×16, vendor 40c → depozito 384c = 24c/adet = vendor'ın %60'ı.
# İlan süresi bilinmiyor; en olası 24 saat (en yüksek oran en uzun süreye karşılık gelir) → 24h = 0.60.
# 2h/8h: süreyle doğrusal varsayım (0.60 × 2/24, 0.60 × 8/24). HEPSİ TAHMİNİ — bir sonraki ilanda süre not edilecek.
DEPOSIT_FACTOR = {2: 0.05, 8: 0.20, 24: 0.60}
DEPOSIT_FACTOR_STATUS = "tahmini (tek gözlem, süre bilinmiyor; 24h varsayıldı)"
MIN_DEPOSIT_COPPER = 1

# static-forever-eu 404 verirse sırayla denenecek item meta namespace'leri.
# static-classic-eu ÇIKARILDI: Slayer's Cape sell_price 17s 9c dedi, oyunda 9s 78c (2026-11-09).
# static-classic1x-eu doğrulandı: Curved Dagger 6s 5c = oyun (tek örnek).
ITEM_NAMESPACES = ["static-forever-eu", "static-classic1x-eu"]
# Vendor fiyatına güvenilen kaynaklar (rapor hesaplarında sadece bunlar kullanılır)
TRUSTED_SELL_SOURCES = ("static-forever-eu", "static-classic1x-eu")

RAW_KEEP_DAYS = 7      # ham gzip JSON saklama süresi (özetler SQLite'ta kalıcı)

DATA_DIR = Path("data")
RAW_DIR = DATA_DIR / "raw" / "houses"
DB_PATH = DATA_DIR / "ahbot.sqlite"
REPORTS_DIR = Path("reports")
