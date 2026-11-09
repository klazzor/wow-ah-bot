"""Kişisel veriyi GİZLİ depoya (klazzor/wow-ah-private) gönderir. WoW dosyaları SADECE okunur.

Yerel kopya: C:\\wow-ah-bot\\_private (açık depoda yok sayılır). İçerik:
- personal/inventory.json      TSM SavedVariables: karakter başına çanta/banka/posta/AH adetleri + para (okuma anı)
- personal/personal.sql.gz     ahbot.sqlite'taki kişisel tablolar (TSM defteri, Auctionator/TSM yerel geçmişi, bilinen tarifler)
                               SQL dökümü; gzip mtime=0 → içerik değişmediyse bayt da aynı, git commit oluşmaz
- reports/...                  claude_rapor*, claude_notes.md, claude_inbox.md, answers/, meta/, ayrıntı raporları (own_goods_* vb.)
- data/user/, PROGRESS.md, CLAUDE.md
Değişiklik yoksa commit/push yapılmaz. Kimlik doğrulama: GitHub CLI (`gh auth login`), global git ayarı değiştirilmez.

Kullanım: python scripts/push_private.py   (Görev Zamanlayıcı: saatte bir + oturum kapanırken)
"""
import gzip
import io
import json
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from ahbot import savedvars  # noqa: E402
from ahbot.analyst import PRIVATE_TABLES  # noqa: E402
from ahbot.config import DB_PATH  # noqa: E402

REPO_URL = "https://github.com/klazzor/wow-ah-private.git"
DEST = ROOT / "_private"
LOG = ROOT / "data" / "logs" / "push_private.log"
GIT_AUTH = ["-c", "credential.helper=", "-c", "credential.helper=!gh auth git-credential"]


def git(*args, check=True):
    r = subprocess.run(["git", *GIT_AUTH, *args], cwd=DEST, capture_output=True, text=True, encoding="utf-8")
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()[:500]}")
    return r.stdout


def write_if_changed(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() == data:
        return False
    path.write_bytes(data)
    return True


def gz(data: bytes) -> bytes:
    buf = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=buf, mtime=0, compresslevel=9) as g:
        g.write(data)
    return buf.getvalue()


def dump_personal_tables() -> bytes:
    con = sqlite3.connect(f"file:{(ROOT / DB_PATH).as_posix()}?mode=ro", uri=True)
    have = {r[0]: r[1] for r in con.execute("SELECT name, sql FROM sqlite_master WHERE type='table'")}
    out = []
    for t in sorted(PRIVATE_TABLES & set(have)):
        out.append(have[t] + ";")
        cols = [r[1] for r in con.execute(f"PRAGMA table_info([{t}])")]
        for row in con.execute(f"SELECT * FROM [{t}] ORDER BY {', '.join(f'[{c}]' for c in cols)}"):
            vals = ", ".join("NULL" if v is None else str(v) if isinstance(v, (int, float))
                             else "'" + str(v).replace("'", "''") + "'" for v in row)
            out.append(f"INSERT INTO [{t}] VALUES ({vals});")
    con.close()
    return ("\n".join(out) + "\n").encode("utf-8")


def mirror(src: Path, dst: Path):
    """src dosya/klasörünü dst'ye aynala (sadece değişenleri yaz, kaynakta olmayanları sil)."""
    if src.is_file():
        write_if_changed(dst, src.read_bytes())
        return
    if not src.exists():
        return
    seen = set()
    for f in src.rglob("*"):
        if f.is_file():
            rel = f.relative_to(src)
            seen.add(rel)
            write_if_changed(dst / rel, f.read_bytes())
    if dst.exists():
        for f in list(dst.rglob("*")):
            if f.is_file() and f.relative_to(dst) not in seen:
                f.unlink()


def main():
    if not (DEST / ".git").exists():
        DEST.mkdir(exist_ok=True)
        subprocess.run(["git", *GIT_AUTH, "clone", "-q", REPO_URL, str(DEST)], check=True)
        if not git("branch", "--show-current").strip():
            git("checkout", "-q", "-b", "main")
    git("pull", "-q", "--ff-only", check=False)

    if savedvars.available():
        sv = savedvars.read_tsm()
        inv = dict(saved_at=sv["saved_at"].isoformat(timespec="seconds"), money=sv["money"],
                   chars={c: {loc: {str(k): v for k, v in sorted(items.items())} for loc, items in locs.items()}
                          for c, locs in sv["chars"].items()})
        write_if_changed(DEST / "personal" / "inventory.json",
                         json.dumps(inv, ensure_ascii=False, indent=1, sort_keys=True).encode("utf-8"))
    write_if_changed(DEST / "personal" / "personal.sql.gz", gz(dump_personal_tables()))

    rep = ROOT / "reports"
    for f in rep.glob("*.md"):
        if f.name != "latest.md":   # latest.md açık depoda (data dalı)
            mirror(f, DEST / "reports" / f.name)
    for sub in ("claude_rapor", "answers", "meta"):
        mirror(rep / sub, DEST / "reports" / sub)
    mirror(ROOT / "data" / "user", DEST / "data" / "user")
    for f in ("PROGRESS.md", "CLAUDE.md"):
        mirror(ROOT / f, DEST / f)

    git("add", "-A")
    if not git("status", "--porcelain").strip():
        print("değişiklik yok, push atlandı")
        return 0
    git("-c", "user.name=klazzor", "-c", "user.email=klazzor@users.noreply.github.com",
        "commit", "-q", "-m", f"kişisel veri {datetime.now():%Y-%m-%d %H:%M}")
    git("push", "-q", "-u", "origin", "HEAD")
    print("push tamam")
    return 0


if __name__ == "__main__":
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as log:
        sys.stdout = sys.stderr = log
        print(f"\n=== {datetime.now():%Y-%m-%d %H:%M:%S} ===")
        try:
            sys.exit(main())
        except Exception as e:
            print("HATA:", e)
            sys.exit(1)
