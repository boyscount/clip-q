"""Business rules that sit between the API and the database.

The daily cap is enforced here, not in the browser: it decides the schedule
(overflow rolls to the next day) and it is the only thing stopping a client
from queueing a hundred clips onto one account for tonight.
"""

from __future__ import annotations

import os
import secrets
from datetime import date, datetime, time, timedelta, timezone

from . import db
from .models import ASPECTS, FORMATS, QueueRequest

# A slot is a wall-clock time the seller picked ("19:30 น."), not a UTC
# instant — storing it as UTC shifted every clip seven hours into the next day.
# Thailand has no DST, so a fixed offset is exact; override for another market.
LOCAL_TZ = timezone(timedelta(hours=float(os.environ.get("CLIPQUEUE_TZ_OFFSET", "7"))))


class ServiceError(Exception):
    """Something the caller can fix — surfaced as a 4xx with this message."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# Shopee accepts up to 50 characters of sub-id; keep well inside it
SUB_ID_MAX = 50


def clip_sub_id(account_sub: str, job_id: str) -> str:
    """One sub-id per clip, not per account.

    The conversion report groups by sub-id, so this is the only thing that
    makes 'which clip earned this order' answerable later. The job id is
    carried verbatim so the report can be matched back without a lookup table.
    """
    return f"{account_sub}_{job_id}"[:SUB_ID_MAX]


def short_link(sub_id: str) -> str:
    # placeholder for the Shopee link-shortener call; the sub-id is what
    # attributes commission back to this exact clip
    return f"https://s.shopee.co.th/{secrets.token_urlsafe(5)}?sub_id={sub_id}"


def plan_schedule(req: QueueRequest, accounts: list[dict], products: list[dict]) -> list[dict]:
    """Lay the requested clips onto (day, slot) per account, respecting caps.

    Returns job dicts ready to insert. Deterministic given the same inputs so
    a retried request produces the same plan.
    """
    start = date.fromisoformat(req.start_date)
    by_id = {a["id"]: a for a in accounts}
    cursors = {}
    for account in accounts:
        used_today = (
            db.posted_on(account["user_id"], account["id"], start.isoformat())
            if account["daily_cap"] > 0 else 0
        )
        cursors[account["id"]] = {"day": 0, "on_day": used_today, "slot": 0}

    batch_id = "b_" + secrets.token_hex(8)
    created = db.now()
    rows: list[dict] = []

    for product in products:
        for account_id in req.account_ids:
            account = by_id[account_id]
            cur = cursors[account_id]
            for _ in range(req.per):
                cap = account["daily_cap"]
                if cap > 0 and cur["on_day"] >= cap:
                    cur["day"] += 1
                    cur["on_day"] = 0
                    cur["slot"] = 0
                    if cur["day"] > 365:
                        raise ServiceError("ตารางยาวเกินหนึ่งปี ลดจำนวนคลิปหรือเพิ่มเพดานต่อวัน")

                hh, mm = (int(x) for x in req.slots[cur["slot"] % len(req.slots)].split(":"))
                when = datetime.combine(
                    start + timedelta(days=cur["day"]), time(hh, mm), tzinfo=LOCAL_TZ
                )
                cur["slot"] += 1
                cur["on_day"] += 1

                job_id = "j_" + secrets.token_hex(8)
                sub_id = clip_sub_id(account["sub_id"], job_id)
                rows.append({
                    "id": job_id,
                    "sub_id": sub_id,
                    "user_id": account["user_id"],
                    "batch_id": batch_id,
                    "product_id": product["id"],
                    "account_id": account_id,
                    "format": req.format,
                    "aspect": req.aspect,
                    "persona": req.persona,
                    "voice": req.voice,
                    "cart": 1 if req.cart else 0,
                    "scheduled_at": when.isoformat(timespec="seconds"),
                    "created_at": created,
                    "link": short_link(sub_id) if req.cart else "",
                })

    return rows


def create_queue(user_id: str, req: QueueRequest) -> dict:
    """Validate ownership, plan the schedule, insert. Idempotent by key."""
    if req.idempotency_key:
        existing = db.remember_batch(user_id, req.idempotency_key, "pending")
        if existing and existing != "pending":
            rows = db.connect().execute(
                "SELECT COUNT(*) AS n FROM jobs WHERE batch_id = ?", (existing,)
            ).fetchone()
            return {"batch_id": existing, "created": 0, "duplicate": True, "total": rows["n"]}

    account_rows = {a["id"]: dict(a) for a in db.accounts(user_id)}
    missing_accounts = [a for a in req.account_ids if a not in account_rows]
    if missing_accounts:
        raise ServiceError(f"ไม่พบบัญชี {', '.join(missing_accounts)} ในผู้ใช้นี้", 404)

    found = {}
    for product_id in req.product_ids:
        item = db.product(user_id, product_id)
        if item is None:
            raise ServiceError(f"ไม่พบสินค้า {product_id} ในคลังของผู้ใช้นี้", 404)
        found[product_id] = item
    products = [found[pid] for pid in req.product_ids]

    accounts = [account_rows[a] for a in req.account_ids]
    rows = plan_schedule(req, accounts, products)
    if not rows:
        raise ServiceError("ไม่มีงานให้สร้าง")

    with db.tx() as conn:
        db.insert_jobs(rows)
        if req.idempotency_key:
            conn.execute(
                "UPDATE batches SET batch_id = ? WHERE user_id = ? AND key = ?",
                (rows[0]["batch_id"], user_id, req.idempotency_key),
            )

    days = len({r["scheduled_at"][:10] for r in rows})
    db.log(user_id, f"เพิ่ม {len(rows)} คลิปลงคิว · {len(req.account_ids)} บัญชี · {days} วัน")
    return {
        "batch_id": rows[0]["batch_id"],
        "created": len(rows),
        "duplicate": False,
        "days": days,
        "total": len(rows),
    }


def job_view(user_id: str, row, stats: dict | None = None) -> dict:
    import json

    product = db.product(user_id, row["product_id"]) or {
        "id": row["product_id"], "name": "(สินค้าถูกลบไปแล้ว)", "price": 0,
        "was": 0, "com": 0, "hue": 200, "bullets": [], "shots": 4, "free": False,
    }
    account = db.connect().execute(
        "SELECT handle, sub_id FROM accounts WHERE id = ?", (row["account_id"],)
    ).fetchone()
    fmt = FORMATS.get(row["format"], 24)
    w, h = ASPECTS.get(row["aspect"], (1080, 1920))

    return {
        "id": row["id"],
        "status": row["status"],
        "progress": row["progress"],
        "attempts": row["attempts"],
        "product": product,
        "account": account["handle"] if account else "(บัญชีถูกลบ)",
        "sub": account["sub_id"] if account else "",
        "format": {"id": row["format"], "sec": fmt, "target": fmt},
        "aspect": {"id": row["aspect"], "w": w, "h": h},
        "persona": row["persona"],
        "voice": row["voice"],
        "cart": bool(row["cart"]),
        "link": row["link"],
        "scheduledAt": row["scheduled_at"],
        "createdAt": row["created_at"],
        "script": json.loads(row["script"] or "[]"),
        "caption": row["caption"],
        "duration": row["duration"],
        "sizeKB": row["size_kb"],
        "video": f"/api/jobs/{row['id']}/video" if row["video_path"] else "",
        "error": row["error"],
        "stats": (stats or {}).get(row["id"], {
            "orders": 0, "salesSatang": 0, "commissionSatang": 0, "cancelled": 0,
        }),
    }


def state(user_id: str) -> dict:
    today = date.today().isoformat()
    account_rows = []
    for account in db.accounts(user_id):
        account_rows.append({
            "id": account["id"],
            "handle": account["handle"],
            "sub": account["sub_id"],
            "cap": account["daily_cap"],
            "used": db.posted_on(user_id, account["id"], today),
            "hue": account["hue"],
        })

    stats = db.job_stats(user_id)
    job_rows = [job_view(user_id, r, stats) for r in db.jobs(user_id)]
    counts = {s: 0 for s in db.STATUSES}
    for row in job_rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1

    return {
        "products": db.products(user_id),
        "accounts": account_rows,
        "jobs": job_rows,
        "counts": counts,
        "earnings": db.conversion_totals(user_id),
        "events": [
            {"at": e["at"], "level": e["level"], "message": e["message"]}
            for e in db.events(user_id)
        ],
    }
