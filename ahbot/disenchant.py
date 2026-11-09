"""Disenchant (DE) beklenen değer modeli.

TABLOLAR Classic Era bilgisinden (Wowpedia/Wowhead) hafızadan girildi — Forever için DOĞRULANMADI.
Doğrulama: oyunda gerçek DE sonuçlarını not et; gerekirse oranları buradan düzelt.
Varsayımlar:
- Yeşil (UNCOMMON) zırh: %75 toz, %20 essence, %5 shard. Silah: %20 toz, %75 essence, %5 shard.
  (ilvl ≤ 15: shard yok → zırh 80/20, silah 20/80)
- Mavi (RARE): %100 shard (Nexus Crystal ihtimali yok sayıldı).
- Adet aralıkları eşit olasılıklı varsayıldı (ortalama = (min+max)/2).
"""

# (max_ilvl, gereken_beceri, (toz_id, min, max), (essence_id, min, max), shard_id_veya_None)
UNCOMMON = [
    (15, 1,   (10940, 1, 2), (10938, 1, 2), None),    # Strange Dust, Lesser Magic Essence
    (20, 1,   (10940, 2, 3), (10939, 1, 2), 10978),   # Greater Magic Essence, Small Glimmering Shard
    (25, 25,  (10940, 4, 6), (10998, 1, 2), 10978),   # Lesser Astral Essence
    (30, 50,  (11083, 1, 2), (11082, 1, 2), 11084),   # Soul Dust, Greater Astral Essence, Large Glimmering Shard
    (35, 75,  (11083, 2, 5), (11134, 1, 2), 11138),   # Lesser Mystic Essence, Small Glowing Shard
    (40, 100, (11137, 1, 2), (11135, 1, 2), 11139),   # Vision Dust, Greater Mystic Essence, Large Glowing Shard
    (45, 125, (11137, 2, 5), (11174, 1, 2), 11177),   # Lesser Nether Essence, Small Radiant Shard
    (50, 150, (11176, 1, 2), (11175, 1, 2), 11178),   # Dream Dust, Greater Nether Essence, Large Radiant Shard
    (55, 175, (11176, 2, 5), (16202, 1, 2), 14343),   # Lesser Eternal Essence, Small Brilliant Shard
    (60, 200, (16204, 1, 2), (16203, 1, 2), 14344),   # Illusion Dust, Greater Eternal Essence, Large Brilliant Shard
    (65, 225, (16204, 2, 5), (16203, 2, 3), 14344),
]
RARE_SHARD = [(25, 10978), (30, 11084), (35, 11138), (40, 11139), (45, 11177), (50, 11178), (55, 14343), (999, 14344)]

# Beklenen isimler (rapor bunları API isimleriyle karşılaştırıp uyuşmazlık uyarısı verir)
MAT_NAMES = {
    10940: "Strange Dust", 10938: "Lesser Magic Essence", 10939: "Greater Magic Essence",
    10998: "Lesser Astral Essence", 11082: "Greater Astral Essence", 10978: "Small Glimmering Shard",
    11083: "Soul Dust", 11084: "Large Glimmering Shard", 11134: "Lesser Mystic Essence",
    11138: "Small Glowing Shard", 11137: "Vision Dust", 11135: "Greater Mystic Essence",
    11139: "Large Glowing Shard", 11174: "Lesser Nether Essence", 11177: "Small Radiant Shard",
    11176: "Dream Dust", 11175: "Greater Nether Essence", 11178: "Large Radiant Shard",
    16202: "Lesser Eternal Essence", 14343: "Small Brilliant Shard", 16204: "Illusion Dust",
    16203: "Greater Eternal Essence", 14344: "Large Brilliant Shard",
}

NOT_DISENCHANTABLE_SLOTS = {"SHIRT", "TABARD", "BAG", "QUIVER", "AMMO", "THROWN"}

# Oyunda "cannot be disenchanted" olduğu doğrulanan / aynı aileden şüphelenilen item'lar.
NOT_DISENCHANTABLE_ITEMS = {
    11288,  # Greater Magic Wand — DOĞRULANDI (oyun, 2026-11-09)
    11287,  # Lesser Magic Wand — aynı Enchanting wand ailesi, şüpheli
    11289,  # Lesser Mystic Wand — aynı aile, şüpheli
    11290,  # Greater Mystic Wand — aynı aile, şüpheli
}


def bracket(ilvl):
    for row in UNCOMMON:
        if ilvl <= row[0]:
            return row
    return None


def required_skill(ilvl):
    b = bracket(ilvl)
    return b[1] if b else None


def outcomes(ilvl, quality, item_class):
    """[(mat_id, olasılık, ortalama_adet)] döndürür; DE edilemiyorsa []."""
    if ilvl is None:
        return []
    if quality == "RARE":
        for mx, shard in RARE_SHARD:
            if ilvl <= mx:
                return [(shard, 1.0, 1.0)]
        return []
    if quality != "UNCOMMON":
        return []
    b = bracket(ilvl)
    if not b:
        return []
    _, _, (d, dmin, dmax), (e, emin, emax), shard = b
    weapon = item_class == "Weapon"
    if shard is None:
        pd, pe, ps = (0.20, 0.80, 0.0) if weapon else (0.80, 0.20, 0.0)
    else:
        pd, pe, ps = (0.20, 0.75, 0.05) if weapon else (0.75, 0.20, 0.05)
    out = [(d, pd, (dmin + dmax) / 2), (e, pe, (emin + emax) / 2)]
    if shard:
        out.append((shard, ps, 1.0))
    return out


def expected_value(outs, mat_net):
    """mat_net: {mat_id: satıştan elde edilecek net birim bakır}. Fiyatı olmayan mat = 0 (temkinli)."""
    return sum(p * q * mat_net.get(m, 0) for m, p, q in outs)
