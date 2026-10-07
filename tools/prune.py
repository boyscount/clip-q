"""Delete rendered mp4s that have already been posted and gone cold.

The job rows stay — the history of what went to which account is the point of
keeping them. Only the file goes, and the row records that it was pruned.

    python tools/prune.py --dry-run
    python tools/prune.py --days 30
    python tools/prune.py --days 30 --also-failed
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server import db, worker  # noqa: E402

PRUNED = "ลบไฟล์แล้วเพื่อประหยัดพื้นที่ · ประวัติยังอยู่"


def candidates(days: int, also_failed: bool) -> list:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    statuses = ["posted"] + (["failed"] if also_failed else [])
    marks = ",".join("?" * len(statuses))
    return db.connect().execute(
        f"SELECT id, user_id, status, video_path, size_kb, finished_at FROM jobs"
        f" WHERE status IN ({marks}) AND video_path != ''"
        f" AND COALESCE(finished_at, created_at) < ?",
        (*statuses, cutoff),
    ).fetchall()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="ลบคลิปเก่าที่อัปไปแล้ว")
    ap.add_argument("--days", type=int, default=30, help="เก่ากว่ากี่วันถึงลบ")
    ap.add_argument("--also-failed", action="store_true", help="ลบของงานที่ล้มเหลวด้วย")
    ap.add_argument("--dry-run", action="store_true", help="ดูว่าจะลบอะไรบ้าง ไม่ลบจริง")
    args = ap.parse_args(argv)

    if args.days < 1:
        print("--days ต้องมากกว่า 0 — กันลบคลิปที่เพิ่งทำเสร็จ", file=sys.stderr)
        return 1

    db.init()
    rows = candidates(args.days, args.also_failed)
    if not rows:
        print(f"ไม่มีคลิปที่เก่ากว่า {args.days} วันให้ลบ")
        return 0

    freed = 0
    gone = 0
    for row in rows:
        path = Path(row["video_path"])
        # only ever unlink inside the renders folder, whatever the row says
        try:
            path.resolve().relative_to(worker.RENDERS.resolve())
        except ValueError:
            print(f"  ข้าม {row['id']} · path อยู่นอกโฟลเดอร์ renders")
            continue

        size = path.stat().st_size if path.exists() else 0
        if args.dry_run:
            print(f"  จะลบ {path.name} · {size / 1024:.0f} KB · {row['status']}")
        else:
            path.unlink(missing_ok=True)
            db.connect().execute(
                "UPDATE jobs SET video_path = '', error = ? WHERE id = ?",
                (PRUNED if row["status"] == "posted" else row["status"], row["id"]),
            )
        freed += size
        gone += 1

    verb = "จะลบ" if args.dry_run else "ลบแล้ว"
    print(f"{verb} {gone} คลิป · คืนพื้นที่ {freed / 1e6:.1f} MB")
    if not args.dry_run:
        db.connect().execute("VACUUM")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
