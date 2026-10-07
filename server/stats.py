"""Pull the Shopee affiliate conversion report and attach it to clips.

Every clip carries its own sub-id, so a report row maps straight back to the
clip that earned it. Rows whose sub-id is not one of ours (links posted by
hand, older links) are still stored, just unmatched — dropping them would make
the totals here disagree with the totals in Shopee's own dashboard.

    python -m server.stats sync --days 7
    python -m server.stats show
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone

from .config import load_env

load_env()

from . import db  # noqa: E402
from .service import LOCAL_TZ  # noqa: E402

# Shopee settles orders days after purchase, so a sync window that only covers
# "since last sync" never sees an order flip from pending to completed.
DEFAULT_DAYS = 14
MAX_DAYS = 90


def baht(satang: int) -> str:
    return f"{satang / 100:,.2f}"


def sync(user_id: str, days: int = DEFAULT_DAYS, client=None) -> dict:
    if not 1 <= days <= MAX_DAYS:
        raise ValueError(f"ช่วงเวลาต้องอยู่ระหว่าง 1 ถึง {MAX_DAYS} วัน")

    if client is None:
        from src.shopee import Client
        client = Client()

    end = datetime.now(tz=LOCAL_TZ)
    start = end - timedelta(days=days)
    rows = client.conversions(int(start.timestamp()), int(end.timestamp()))

    now = db.now()
    prepared = []
    matched = unmatched = 0
    for row in rows:
        sub_id = row.get("sub_id") or ""
        job = db.job_by_sub(user_id, sub_id) if sub_id else None
        if job:
            matched += 1
        else:
            unmatched += 1
        prepared.append({
            "id": str(row["conversion_id"]),
            "user_id": user_id,
            "job_id": job["id"] if job else None,
            "sub_id": sub_id,
            "item_id": row.get("item_id", ""),
            "item_name": row.get("item_name", ""),
            "qty": int(row.get("qty") or 1),
            "amount_satang": int(row.get("amount_satang") or 0),
            "commission_satang": int(row.get("commission_satang") or 0),
            "status": row.get("status", ""),
            "purchase_time": _iso(row.get("purchase_time")),
            "synced_at": now,
        })

    new, updated = db.upsert_conversions(prepared)
    db.log(user_id,
           f"ดึงรายงาน {days} วัน · {len(prepared)} รายการ · ใหม่ {new} อัปเดต {updated}"
           f" · จับคู่คลิปได้ {matched}")
    return {
        "window_days": days,
        "rows": len(prepared),
        "new": new,
        "updated": updated,
        "matched": matched,
        "unmatched": unmatched,
    }


def _iso(value) -> str:
    """The report gives a unix timestamp; store it as local-time ISO like
    everything else in this database."""
    if not value:
        return ""
    try:
        return datetime.fromtimestamp(int(value), tz=LOCAL_TZ).isoformat(timespec="seconds")
    except (TypeError, ValueError, OSError):
        return str(value)[:40]


def summary(user_id: str, limit: int = 15) -> dict:
    stats = db.job_stats(user_id)
    totals = db.conversion_totals(user_id)

    ranked = []
    for job_id, figures in stats.items():
        row = db.job(user_id, job_id)
        if row is None:
            continue
        product = db.product(user_id, row["product_id"])
        ranked.append({
            "job_id": job_id,
            "product": product["name"] if product else row["product_id"],
            "account": row["account_id"],
            "format": row["format"],
            "status": row["status"],
            **figures,
        })
    ranked.sort(key=lambda r: r["commissionSatang"], reverse=True)
    return {"totals": totals, "top": ranked[:limit], "clips_with_sales": len(ranked)}


def cmd_sync(args) -> int:
    db.init()
    user = db.user_by_email(args.email) if args.email else _only_user()
    try:
        result = sync(user["id"], args.days)
    except Exception as exc:  # noqa: BLE001
        print(f"ดึงรายงานไม่สำเร็จ: {exc}", file=sys.stderr)
        return 1
    print(f"ช่วง {result['window_days']} วัน · {result['rows']} รายการ"
          f" · ใหม่ {result['new']} · อัปเดต {result['updated']}")
    print(f"จับคู่กับคลิปได้ {result['matched']} · ไม่ใช่ลิงก์จากระบบนี้ {result['unmatched']}")
    return 0


def cmd_show(args) -> int:
    db.init()
    user = db.user_by_email(args.email) if args.email else _only_user()
    data = summary(user["id"])
    t = data["totals"]

    print(f"\nรวมทั้งหมด · ออเดอร์ {t['orders']} · ค่าคอมฯ {baht(t['commissionSatang'])} บาท")
    print(f"ดึงล่าสุด {t['lastSync'] or '-'} · รายการที่จับคู่คลิปไม่ได้ {t['unmatched']}")

    if not data["top"]:
        print("\nยังไม่มีออเดอร์ที่ผูกกับคลิป — ดึงรายงานด้วย `sync` ก่อน\n")
        return 0

    print(f"\nคลิปที่ทำเงิน ({data['clips_with_sales']} คลิป)")
    print(f"  {'คลิป':<20} {'สินค้า':<34} {'ออเดอร์':>7} {'ค่าคอมฯ':>11}  ยกเลิก")
    for row in data["top"]:
        print(f"  {row['job_id']:<20} {row['product'][:32]:<34} {row['orders']:>7}"
              f" {baht(row['commissionSatang']):>11}  {row['cancelled']}")
    print()
    return 0


def _only_user():
    rows = db.connect().execute("SELECT * FROM users ORDER BY created_at").fetchall()
    if not rows:
        sys.exit("ยังไม่มีผู้ใช้ — สร้างด้วย `python -m server.admin create-user`")
    if len(rows) > 1:
        sys.exit("มีผู้ใช้หลายคน ระบุด้วย --email")
    return rows[0]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="server.stats", description="ผลลัพธ์จาก Shopee Affiliate")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("sync", help="ดึงรายงาน conversion มาเก็บ")
    s.add_argument("--days", type=int, default=DEFAULT_DAYS,
                   help=f"ย้อนหลังกี่วัน (ค่าเริ่มต้น {DEFAULT_DAYS} เพราะออเดอร์เปลี่ยนสถานะทีหลัง)")
    s.add_argument("--email")
    s.set_defaults(fn=cmd_sync)

    v = sub.add_parser("show", help="ดูสรุปว่าคลิปไหนทำเงิน")
    v.add_argument("--email")
    v.set_defaults(fn=cmd_show)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
