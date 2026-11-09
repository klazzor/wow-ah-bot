"""GitHub Actions için başlangıç veritabanı: data/seed/ahbot_public.sqlite.gz
ahbot.sqlite'ın kopyası, KİŞİSEL tablolar (analyst.PRIVATE_TABLES) silinmiş. Açık depoda Release dosyası olarak
yayınlanır; Actions önbelleği (actions/cache) boşsa workflow bunu indirir.

Kullanım: python -m scripts.make_seed
"""
import gzip
import shutil
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from ahbot.analyst import PRIVATE_TABLES  # noqa: E402
from ahbot.config import DB_PATH  # noqa: E402

OUT_DIR = ROOT / "data" / "seed"


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tmp = OUT_DIR / "ahbot_public.sqlite"
    tmp.unlink(missing_ok=True)
    src = sqlite3.connect(f"file:{(ROOT / DB_PATH).as_posix()}?mode=ro", uri=True)
    dst = sqlite3.connect(tmp)
    src.backup(dst)
    src.close()
    for (t,) in dst.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
        if t in PRIVATE_TABLES:
            dst.execute(f"DROP TABLE [{t}]")
    dst.commit()
    left = {r[0] for r in dst.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert not (left & PRIVATE_TABLES), left & PRIVATE_TABLES
    dst.execute("VACUUM")
    dst.close()
    gz = OUT_DIR / "ahbot_public.sqlite.gz"
    with open(tmp, "rb") as f, gzip.open(gz, "wb", compresslevel=6) as g:
        shutil.copyfileobj(f, g, 1 << 20)
    print(f"seed: {tmp.stat().st_size / 1e6:.0f} MB → {gz.name} {gz.stat().st_size / 1e6:.0f} MB; tablolar: {sorted(left)}")
    tmp.unlink()


if __name__ == "__main__":
    main()
