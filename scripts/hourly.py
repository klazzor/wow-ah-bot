"""Görev Zamanlayıcı'nın saatlik çalıştırdığı giriş noktası.
Snapshot çeker, yeni item'ların meta'sını tamamlar, eski ham dosyaları temizler.
Çıktı data/logs/collector.log'a yazılır (pythonw ile pencere açılmaz).
"""
import os
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

LOG = ROOT / "data" / "logs" / "collector.log"
LOG.parent.mkdir(parents=True, exist_ok=True)
PUBLIC = os.getenv("AHBOT_PUBLIC") == "1"   # GitHub Actions: SavedVariables yok, kişisel adımlar atlanır, log ekrana
if not PUBLIC:
    sys.stdout = sys.stderr = open(LOG, "a", encoding="utf-8", buffering=1)

T0 = time.time()
print(f"\n=== {datetime.now():%Y-%m-%d %H:%M:%S} ===")
try:
    from ahbot import collector, items
    from ahbot.config import RAW_KEEP_DAYS

    collector.run()
    items.fill()
    collector.prune_raw(RAW_KEEP_DAYS)
    try:
        from ahbot import tsm
        tsm.run()   # realm: 3 saatte bir, region: günde bir (aralık dolmadıysa atlar)
    except Exception:
        traceback.print_exc()
        print("TSM indirmesi başarısız (collector etkilenmedi)")
    from ahbot import savedvars
    have_sv = savedvars.available()
    if not have_sv:
        print("SavedVariables yok/kapalı → defter, yerel geçmiş ve envanter adımları atlandı")
    try:
        if have_sv:
            from ahbot import ledger
            ledger.run().close()   # TSM Accounting defteri (TradeSkillMaster.lua SADECE okunur; değişmediyse atlar) — rapordan önce
    except Exception:
        traceback.print_exc()
        print("TSM defter içe aktarımı HATA (veri toplama etkilenmedi)")
    try:
        from scripts import hourly_report
        hourly_report.main()   # reports/latest.md + reports/hourly/YYYYMMDD_HH.md
    except Exception:
        traceback.print_exc()
        print("Özet rapor HATA (veri toplama etkilenmedi)")
    try:
        if have_sv:
            from ahbot import history
            history.run().close()   # Auctionator + TSM AppData yerel geçmişi (WoW dosyaları SADECE okunur; değişmediyse atlar)
    except Exception:
        traceback.print_exc()
        print("Yerel geçmiş içe aktarımı HATA (veri toplama etkilenmedi)")
    try:
        from ahbot import analyst
        analyst.run(public=PUBLIC)   # auction_flow özeti → eski auction_seen silme (48 sa) → data/analyst.sqlite
    except Exception:
        traceback.print_exc()
        print("Analist kopyası HATA (veri toplama etkilenmedi)")
    if PUBLIC:
        try:
            from scripts import export_summary
            export_summary.main(days=2)   # data/summary/YYYY-MM-DD.csv.gz (son 2 gün; eskiler data dalında kalıcı)
        except Exception:
            traceback.print_exc()
            print("Günlük özet HATA")
    # Gelen kutusu "soru" cevapları: 7 günden eskileri sil (reports/answers/*.md — kullanıcının belirlediği saklama)
    answers = ROOT / "reports" / "answers"
    if answers.exists():
        cutoff = time.time() - 7 * 86400
        old = [f for f in answers.glob("*.md") if f.stat().st_mtime < cutoff]
        for f in old:
            f.unlink()
        if old:
            print(f"{len(old)} eski cevap dosyası silindi (reports/answers, > 7 gün)")
    if PUBLIC:
        latest = ROOT / "reports" / "latest.md"
        if latest.exists():
            with open(latest, "a", encoding="utf-8") as f:
                f.write(f"_Toplam çalışma süresi (GitHub Actions): {time.time() - T0:.0f} sn_\n")
    print(f"OK ({time.time() - T0:.0f} sn)")
except Exception:
    traceback.print_exc()
    print("HATA")
    sys.exit(1)
