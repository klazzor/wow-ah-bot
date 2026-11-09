"""TSM SavedVariables'tan envanter ve para okur (SADECE OKUR, dosyayı asla değiştirmez).
Dosya oyundan çıkışta ya da /reload'da yazılır → içerik o anki durumdur.

Kullanım: python -m ahbot.savedvars   # özet yazdırır
"""
import os
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path

WOW_DIR = Path(os.getenv("WOW_DIR", r"C:\Program Files (x86)\World of Warcraft\_forever_"))


def _find_tsm_sv():
    """Hesap klasörünü koda yazmadan bul (açık depoda hesap numarası olmasın). WOW_ACCOUNT ortam değişkeni önceliklidir."""
    acc = os.getenv("WOW_ACCOUNT")
    if acc:
        return WOW_DIR / "WTF" / "Account" / acc / "SavedVariables" / "TradeSkillMaster.lua"
    hits = sorted((WOW_DIR / "WTF" / "Account").glob("*/SavedVariables/TradeSkillMaster.lua"),
                  key=lambda p: p.stat().st_mtime, reverse=True) if (WOW_DIR / "WTF" / "Account").exists() else []
    return hits[0] if hits else WOW_DIR / "WTF" / "Account" / "_yok_" / "SavedVariables" / "TradeSkillMaster.lua"


TSM_SV = _find_tsm_sv()


def available(path=TSM_SV):
    """SavedVariables okunabilir mi? GitHub Actions'ta (AHBOT_PUBLIC=1) her zaman False → kişisel adımlar atlanır."""
    return os.getenv("AHBOT_PUBLIC") != "1" and Path(path).exists()

LOCATIONS = ("bagQuantity", "bankQuantity", "reagentBankQuantity", "mailQuantity", "auctionQuantity")
_KEY = re.compile(r'^\["s@(?P<char>[^@]+)@internalData@(?P<loc>\w+)"\]\s*=\s*(?P<rest>.*)$')
_ITEM = re.compile(r'^\["i:(\d+)[^"]*"\]\s*=\s*(\d+),?$')


def read_tsm(path=TSM_SV):
    """Döner: dict(chars={char: {loc: {item_id: qty}}}, money={char: copper}, saved_at=datetime)"""
    text = path.read_text(encoding="utf-8", errors="replace").splitlines()
    chars = defaultdict(lambda: defaultdict(dict))
    money = {}
    cur = None
    for line in text:
        line = line.strip()
        if cur:
            if line.startswith("}"):
                cur = None
                continue
            m = _ITEM.match(line)
            if m:
                iid, q = int(m.group(1)), int(m.group(2))
                d = chars[cur[0]][cur[1]]
                d[iid] = d.get(iid, 0) + q
            continue
        m = _KEY.match(line)
        if not m:
            continue
        char, loc, rest = m.group("char"), m.group("loc"), m.group("rest")
        if loc in LOCATIONS and rest.startswith("{") and not rest.startswith("{}"):
            cur = (char, loc)
        elif loc == "money":
            money[char] = int(rest.rstrip(","))
    return dict(chars={c: dict(v) for c, v in chars.items()}, money=money,
                saved_at=datetime.fromtimestamp(path.stat().st_mtime))


def vendor_buy_prices(path=TSM_SV):
    """csvBuys'ta source=Vendor olan alımlardan {item_id: birim fiyat} — vendor'ın sattığının KANITI."""
    text = path.read_text(encoding="utf-8", errors="replace")
    m = re.search(r'\["r@[^@]+@internalData@csvBuys"\]\s*=\s*"((?:[^"\\]|\\.)*)"', text)
    out = {}
    if not m:
        return out
    for row in m.group(1).split("\\n")[1:]:
        f = row.split(",")
        if len(f) >= 8 and f[7] == "Vendor":
            iid, price = int(f[0].split(":")[1]), int(f[3])
            out[iid] = min(price, out.get(iid, price))
    return out


def inventory(path=TSM_SV, locations=("bagQuantity", "bankQuantity", "reagentBankQuantity", "mailQuantity")):
    """Tüm karakterler ve seçili konumlar toplamı: {item_id: qty}. AH'deki ilanlar hariç (zaten satışta)."""
    sv = read_tsm(path)
    tot = defaultdict(int)
    for locs in sv["chars"].values():
        for loc, items in locs.items():
            if loc in locations:
                for iid, q in items.items():
                    tot[iid] += q
    return dict(tot), sv


if __name__ == "__main__":
    inv, sv = inventory()
    print("Kayıt zamanı:", sv["saved_at"])
    for c, locs in sv["chars"].items():
        print(c, {loc: len(items) for loc, items in locs.items()}, "para:", sv["money"].get(c))
    print("Toplam farklı item:", len(inv), "| toplam adet:", sum(inv.values()))
