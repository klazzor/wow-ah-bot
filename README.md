# wow-ah-bot

WoW: Forever (EU, PvP Horde, açık artırma evi 113) için saatlik açık artırma verisi toplayıcısı ve analiz araçları.
Sistem **sadece veri okur ve liste üretir**; oyun içinde hiçbir işlem otomatikleştirilmez.

- **Veri kaynakları:** Blizzard Battle.net API (`dynamic-forever-eu`, `static-forever-eu`) ve TSM'in herkese açık CSV'leri.
- **Toplama:** GitHub Actions, her saat :20'de (`.github/workflows/hourly.yml`, `AHBOT_PUBLIC=1` → kişisel veri yok).
- **Çıktılar:** [`data` dalı](../../tree/data):
  - `reports/latest.md`: saatlik piyasa özeti.
  - `reports/hourly/`: son 3 günün saatlik özetleri.
  - `data/analyst.sqlite`: son 14 gün, salt okunur analiz kopyası (şema: `data/analyst_schema.md`).
  - `data/summary/YYYY-MM-DD.csv.gz`: günlük item fiyat özetleri (UTC; fiyatlar bakır cinsinden).
- **Kalıcılık:** `ahbot.sqlite` actions/cache ile taşınır; önbellek boşsa `seed` Release dosyasından başlar.

Kişisel veriler (envanter, para, TSM defteri, Auctionator/TSM yerel geçmişi) ayrı, gizli bir depoda tutulur.
