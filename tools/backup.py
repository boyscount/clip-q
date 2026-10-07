"""Back up the ClipQueue database.

Uses SQLite's own backup API, not a file copy: with WAL on, copying the .db
while the worker is writing gives you a torn file that looks fine until the
day you need it.

    python tools/backup.py                 # one snapshot, keep 14 days
    python tools/backup.py --keep 30
    python tools/backup.py --verify-only   # check the newest backup opens
"""

from __future__ import annotations

import argparse
import gzip
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server import db  # noqa: E402

BACKUPS = Path(ROOT / "data" / "backups")


def snapshot(dest: Path) -> Path:
    """Consistent copy taken through sqlite3's backup API, then gzipped."""
    with tempfile.TemporaryDirectory() as tmp:
        raw = Path(tmp) / "snapshot.db"
        source = sqlite3.connect(db.DB_PATH)
        target = sqlite3.connect(raw)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()

        dest.parent.mkdir(parents=True, exist_ok=True)
        with raw.open("rb") as fin, gzip.open(dest, "wb", compresslevel=6) as fout:
            shutil.copyfileobj(fin, fout)
    return dest


def verify(path: Path) -> tuple[bool, str]:
    """Open the backup and run an integrity check — a backup nobody has
    restored is a guess, not a backup."""
    with tempfile.TemporaryDirectory() as tmp:
        raw = Path(tmp) / "check.db"
        with gzip.open(path, "rb") as fin, raw.open("wb") as fout:
            shutil.copyfileobj(fin, fout)
        conn = sqlite3.connect(raw)
        try:
            result = conn.execute("PRAGMA integrity_check").fetchone()[0]
            if result != "ok":
                return False, result
            users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            jobs = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
            return True, f"ผู้ใช้ {users} · งาน {jobs}"
        except sqlite3.DatabaseError as exc:
            return False, str(exc)
        finally:
            conn.close()


def prune(folder: Path, keep: int) -> int:
    files = sorted(folder.glob("clipqueue-*.db.gz"))
    removed = 0
    for old in files[:-keep] if keep > 0 else []:
        old.unlink()
        removed += 1
    return removed


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="สำรองฐานข้อมูล ClipQueue")
    ap.add_argument("--keep", type=int, default=14, help="เก็บไว้กี่ไฟล์ (0 = ไม่ลบ)")
    ap.add_argument("--out", type=Path, default=BACKUPS, help="โฟลเดอร์ปลายทาง")
    ap.add_argument("--verify-only", action="store_true", help="แค่ตรวจไฟล์ล่าสุด ไม่สำรองใหม่")
    args = ap.parse_args(argv)
    folder: Path = args.out

    if args.verify_only:
        files = sorted(folder.glob("clipqueue-*.db.gz"))
        if not files:
            print("ยังไม่มีไฟล์สำรอง", file=sys.stderr)
            return 1
        ok, detail = verify(files[-1])
        print(f"{'ผ่าน' if ok else 'เสียหาย'} · {files[-1].name} · {detail}")
        return 0 if ok else 1

    if not db.DB_PATH.exists():
        print(f"ไม่พบฐานข้อมูลที่ {db.DB_PATH}", file=sys.stderr)
        return 1

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = folder / f"clipqueue-{stamp}.db.gz"
    for n in range(2, 100):  # two runs in the same second must not overwrite
        if not dest.exists():
            break
        dest = folder / f"clipqueue-{stamp}-{n}.db.gz"
    snapshot(dest)

    ok, detail = verify(dest)
    size = dest.stat().st_size / 1024
    if not ok:
        print(f"สำรองแล้วแต่ตรวจไม่ผ่าน: {detail}", file=sys.stderr)
        return 1

    removed = prune(folder, args.keep)
    print(f"สำรองแล้ว {dest} · {size:.0f} KB · {detail}"
          + (f" · ลบของเก่า {removed} ไฟล์" if removed else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
