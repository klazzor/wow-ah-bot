"""Ortak piyasa değerleme + "şüpheli fırsat" kuralı (tüm raporlar bunu kullanır).

Değerleme (birim başına, bakır):
- NPC: vendor sell_price (sadece güvenilir kaynaklar; classic1x = fallback → uyarı).
- AH: P(satış) × (en ucuz − 1c) × (1 − pay) + P(satılmama) × (NPC − depozito).
  P(satılmama) = 1 − TSM bölge saleRate (yoksa config varsayılanı).
- DE: ahbot.disenchant beklenen değeri (tablo DOĞRULANMADI → uyarı).
"""
from ahbot import disenchant as de
from ahbot import savedvars, tsm
from ahbot.analysis import SLOW_SOLD_PER_DAY, p_unsold
from ahbot.config import (AH_CUT_FACTION, DEFAULT_LIST_HOURS, DEPOSIT_FACTOR, ENCHANTING_SKILL, HOUSE_ID,
                          TRUSTED_SELL_SOURCES)

# Şüpheli fırsat eşikleri
OVERSUPPLY_LISTINGS = 100        # satış hızı BİLİNMİYORSA: ev 113'te bu kadar ya da daha fazla ilan
OVERSUPPLY_DAYS = 3              # AH'deki adet > bölge günlük satışın 3 katı (commodity'lerde ilan sayısı tek başına anlamsız)
TOO_GOOD_ROI = 1.0               # beklenen kâr / maliyet > %100 ...
TOO_GOOD_MIN_LISTINGS = 20       # ... ve arz da bolsa (Greater Magic Wand dersi)
TEST_FIRST = "→ önce 1-2 tane ile test et"


def money(c):
    if c is None:
        return "—"
    c = int(round(c))
    neg, c = c < 0, abs(c)
    g, s, b = c // 10000, (c // 100) % 100, c % 100
    return ("-" if neg else "") + (f"{g}g " if g else "") + (f"{s}s " if g or s else "") + f"{b}c"


class Market:
    def __init__(self, con):
        self.con = con
        sids = [r[0] for r in con.execute("SELECT MAX(id) FROM snapshots WHERE house_id=? GROUP BY kind", (HOUSE_ID,))]
        ph = ",".join("?" * len(sids))
        self.summ = {r[0]: r[1:] for r in con.execute(
            f"SELECT item_id, min_unit, median_unit, qty, listing_count FROM item_summary WHERE snapshot_id IN ({ph})", sids)}
        self.meta = {r[0]: dict(zip(("name", "quality", "cls", "sub", "sell", "ilvl", "slot", "source", "buy"), r[1:]))
                     for r in con.execute("SELECT item_id, name, quality, item_class, item_subclass, sell_price, "
                                          "item_level, inventory_type, source, purchase_price FROM items")}
        self.tsm = tsm.latest(con)
        try:
            self.vendor_buy = savedvars.vendor_buy_prices()
        except OSError:
            self.vendor_buy = {}

    # --- temel bilgiler ---
    def name(self, iid):
        return (self.meta.get(iid) or {}).get("name") or (self.tsm.get(iid) or {}).get("name") or f"item {iid}"

    def spd(self, iid):
        return (self.tsm.get(iid) or {}).get("g_spd")

    def listings(self, iid):
        s = self.summ.get(iid)
        return (s[3], s[2]) if s else (0, 0)          # (ilan sayısı, toplam adet)

    def ah_min(self, iid):
        s = self.summ.get(iid)
        return s[0] if s else None

    def ah_median(self, iid):
        s = self.summ.get(iid)
        return s[1] if s else None

    # --- değerleme ---
    def vendor_sell(self, iid):
        """(birim NPC fiyatı, uyarı) — uyarı boş değilse fiyat doğrulanmamış kaynaktan."""
        m = self.meta.get(iid) or {}
        if m.get("source") in TRUSTED_SELL_SOURCES and m.get("sell"):
            return m["sell"], ("" if m["source"] == "static-forever-eu" else "vendor fiyatı fallback kaynaktan")
        return 0, ""

    def ah_net(self, iid, price_override=None):
        """(beklenen net birim değer, notlar, doğrulanmamış_mı) — price_override: ilan fiyatı (ör. reset şüphesinde referans)"""
        notes, unverified = [], False
        s = self.summ.get(iid)
        t = self.tsm.get(iid) or {}
        if price_override:
            price = price_override
        elif s and s[0]:
            price = s[0] - 1
            if s[3] == 1:
                notes.append("AH'de tek ilan")
                unverified = True
        elif t.get("r_mv"):
            price = t["r_mv"]
            notes.append("ev 113'te ilan yok → TSM marketValue")
            unverified = True
        else:
            return None, ["AH fiyatı yok"], True
        sell = (self.meta.get(iid) or {}).get("sell") or 0
        _, pu = p_unsold(t)
        dep = sell * DEPOSIT_FACTOR[DEFAULT_LIST_HOURS]
        if pu >= 0.5:
            notes.append(f"satılmama ihtimali %{pu*100:.0f}")
        vend, _ = self.vendor_sell(iid)
        return (1 - pu) * price * (1 - AH_CUT_FACTION) + pu * (vend - dep), notes, unverified

    def buy_cost(self, iid, use_median=False):
        """Malzeme alım birim maliyeti: vendor (TSM alış geçmişinde vendor'dan alınmış = kanıt) ya da AH."""
        opts = []
        if iid in self.vendor_buy:
            opts.append((self.vendor_buy[iid], "vendor"))
        s = self.summ.get(iid)
        if s and s[0]:
            opts.append((s[1] if use_median and s[1] else s[0], "AH"))
        return min(opts) if opts else (None, None)

    def de_ev(self, iid, skill=ENCHANTING_SKILL):
        m = self.meta.get(iid) or {}
        if m.get("cls") not in ("Weapon", "Armor") or iid in de.NOT_DISENCHANTABLE_ITEMS \
                or m.get("slot") in de.NOT_DISENCHANTABLE_SLOTS or m.get("ilvl") is None:
            return None
        req = de.required_skill(m["ilvl"])
        if req is None or req > skill:
            return None
        outs = de.outcomes(m["ilvl"], m["quality"], m["cls"])
        if not outs:
            return None
        net = {mid: max(0, self.ah_net(mid)[0] or 0) for mid, _, _ in outs}
        return de.expected_value(outs, net)

    def best_value(self, iid, ah_price=None):
        """(en iyi birim değer, 'AH'|'NPC'|'DE', notlar, doğrulanmamış_mı, {seçenek: değer})"""
        cands, allv = [], {}
        ah, notes, unv = self.ah_net(iid, ah_price)
        if ah is not None:
            cands.append((ah, "AH", notes, unv))
            allv["AH"] = ah
        vs, vnote = self.vendor_sell(iid)
        if vs:
            cands.append((vs, "NPC", [vnote] if vnote else [], bool(vnote)))
            allv["NPC"] = vs
        ev = self.de_ev(iid)
        if ev:
            cands.append((ev, "DE", ["DE tablosu doğrulanmadı"], True))
            allv["DE"] = ev
        if not cands:
            return None, None, ["değer yok"], True, allv
        v, how, n, u = max(cands, key=lambda c: c[0])
        return v, how, n, u, allv

    # --- şüpheli fırsat kuralı ---
    def suspicious(self, iid, how=None, unverified=False, cost=None, profit=None):
        """Uyarı listesi; boş değilse son eleman TEST_FIRST."""
        w = []
        spd = self.spd(iid)
        n, qty = self.listings(iid)
        if how in (None, "AH"):
            if spd is not None and spd < SLOW_SOLD_PER_DAY:
                w.append(f"yavaş satış ({spd:g}/gün, bölge)")
            elif spd is None:
                w.append("satış hızı bilinmiyor")
            if (spd and qty > OVERSUPPLY_DAYS * spd) or (spd is None and n >= OVERSUPPLY_LISTINGS):
                w.append(f"AH'de aşırı arz ({qty} adet" + (f" ≈ {qty/spd:.0f} günlük bölge satışı)" if spd else f", {n} ilan)"))
        if unverified:
            w.append("fiyat doğrulanmamış kaynaktan")
        if how == "DE":
            w.append("DE tablosu doğrulanmadı")
        if cost and profit and profit / cost > TOO_GOOD_ROI and n >= TOO_GOOD_MIN_LISTINGS:
            w.append(f"kâr şüpheli derecede yüksek (%{100*profit/cost:.0f}) ve arz bol")
        if w:
            w.append(TEST_FIRST)
        return w


def best_product_value(mk, pid):
    """Geriye uyumluluk: (değer, nasıl, notlar)."""
    v, how, notes, unv, _ = mk.best_value(pid)
    return v, how, notes + (["fiyat doğrulanmamış kaynaktan"] if unv and how != "DE" else [])
