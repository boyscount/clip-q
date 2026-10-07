"""Command line admin for ClipQueue — users, tokens, accounts.

There is no signup page, by design: for a single-operator install the fewest
moving parts is a CLI that writes straight to the database.

    python -m server.admin create-user --email you@example.com --name "ร้านดีดีช้อป"
    python -m server.admin add-account --email you@example.com --handle @myshop --cap 3
    python -m server.admin rotate-token --email you@example.com
    python -m server.admin list
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys
from pathlib import Path

from .config import load_env

load_env()

from . import db  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SEED = Path(__file__).resolve().parent / "seed_products.json"


def _user_or_die(email: str):
    row = db.user_by_email(email)
    if row is None:
        sys.exit(f"ไม่พบผู้ใช้ {email} — ดูรายชื่อด้วย `python -m server.admin list`")
    return row


def show_token(token: str, email: str) -> None:
    print("\n" + "─" * 58)
    print(f"  token ของ {email}")
    print(f"  {token}")
    print("─" * 58)
    print("  เก็บไว้ให้ดี — เก็บลงฐานข้อมูลแบบ hash จึงดูย้อนหลังไม่ได้")
    print(f"  เปิดใช้งานครั้งแรก:  http://127.0.0.1:8787/?token={token}")
    print("  หลังจากนั้นเบราว์เซอร์จะจำไว้ให้เอง\n")


def cmd_create_user(args) -> int:
    if db.user_by_email(args.email):
        sys.exit(f"มีผู้ใช้ {args.email} อยู่แล้ว — เปลี่ยน token ด้วย rotate-token")

    user_id, token = db.create_user(args.email, args.name, args.token)

    if not args.no_accounts:
        db.upsert_account(user_id, args.handle, args.sub, args.cap, 20)
        print(f"สร้างบัญชี Shopee: {args.handle} · sub-id {args.sub} · เพดาน {args.cap or 'ไม่จำกัด'}/วัน")

    load_catalogue(user_id)
    db.log(user_id, f"สร้างผู้ใช้ {args.email}")
    show_token(token, args.email)
    return 0


def load_catalogue(user_id: str) -> int:
    """Fill the product catalogue, reporting instead of dying.

    A write can lose a race with a running server holding the database; the
    user and the token are already created at that point, so failing the whole
    command here would leave a half-made account with no obvious fix.
    """
    catalogue = ROOT / "app" / "products.json"
    source = catalogue if catalogue.exists() else SEED
    if not source.exists():
        print("ไม่พบไฟล์คลังสินค้า — ใส่ทีหลังด้วย `seed-products`")
        return 0
    try:
        n = db.replace_products(user_id, json.loads(source.read_text(encoding="utf-8")))
        print(f"ใส่คลังสินค้า {n} รายการ จาก {source.name}")
        return n
    except Exception as exc:  # noqa: BLE001
        print(f"ใส่คลังสินค้าไม่สำเร็จ: {exc}")
        print("  มักเกิดจากเซิร์ฟเวอร์กำลังรันอยู่และจับฐานข้อมูลไว้")
        print("  ปิดเซิร์ฟเวอร์แล้วรัน: python -m server.admin seed-products --email <อีเมล>")
        return 0


def cmd_seed_products(args) -> int:
    user = _user_or_die(args.email)
    n = load_catalogue(user["id"])
    if n:
        print(f"ตอนนี้มีสินค้า {len(db.products(user['id']))} รายการ")
    return 0 if n else 1


def cmd_rotate(args) -> int:
    user = _user_or_die(args.email)
    token = args.token or secrets.token_urlsafe(24)
    db.connect().execute(
        "UPDATE users SET token_hash = ? WHERE id = ?", (db.hash_token(token), user["id"])
    )
    db.log(user["id"], "เปลี่ยน token", "warn")
    print("token เดิมใช้ไม่ได้แล้ว")
    show_token(token, args.email)
    return 0


def cmd_add_account(args) -> int:
    user = _user_or_die(args.email)
    account_id = db.upsert_account(user["id"], args.handle, args.sub, args.cap, args.hue)
    print(f"{args.handle} · sub-id {args.sub} · เพดาน {args.cap or 'ไม่จำกัด'}/วัน · id {account_id}")
    return 0


def cmd_list(args) -> int:
    users = db.connect().execute("SELECT * FROM users ORDER BY created_at").fetchall()
    if not users:
        print("ยังไม่มีผู้ใช้")
        return 0

    for user in users:
        counts = db.connect().execute(
            "SELECT status, COUNT(*) n FROM jobs WHERE user_id = ? GROUP BY status",
            (user["id"],),
        ).fetchall()
        summary = " · ".join(f"{r['status']} {r['n']}" for r in counts) or "ยังไม่มีงาน"
        products = db.connect().execute(
            "SELECT COUNT(*) n FROM products WHERE user_id = ?", (user["id"],)
        ).fetchone()["n"]
        print(f"\n{user['name']} <{user['email']}>  ({user['id']})")
        print(f"  สินค้า {products} รายการ · {summary}")
        for account in db.accounts(user["id"]):
            print(f"  - {account['handle']:20s} sub-id {account['sub_id']:12s} "
                  f"เพดาน {account['daily_cap'] or 'ไม่จำกัด'}/วัน")
    print()
    return 0


def main(argv=None) -> int:
    db.init()
    ap = argparse.ArgumentParser(prog="server.admin", description="จัดการผู้ใช้และบัญชี ClipQueue")
    sub = ap.add_subparsers(dest="cmd", required=True)

    new = sub.add_parser("create-user", help="สร้างผู้ใช้ใหม่พร้อม token")
    new.add_argument("--email", required=True)
    new.add_argument("--name", required=True)
    new.add_argument("--token", help="กำหนด token เอง (ปกติปล่อยให้สุ่ม)")
    new.add_argument("--handle", default="@myshop", help="บัญชี Shopee แรก")
    new.add_argument("--sub", default="sub_main", help="sub-id สำหรับแยกยอดค่าคอมฯ")
    new.add_argument("--cap", type=int, default=3, help="เพดานคลิป/วัน (0 = ไม่จำกัด)")
    new.add_argument("--no-accounts", action="store_true")
    new.set_defaults(fn=cmd_create_user)

    rot = sub.add_parser("rotate-token", help="เปลี่ยน token (ของเดิมใช้ไม่ได้ทันที)")
    rot.add_argument("--email", required=True)
    rot.add_argument("--token")
    rot.set_defaults(fn=cmd_rotate)

    acc = sub.add_parser("add-account", help="เพิ่มหรือแก้บัญชี Shopee")
    acc.add_argument("--email", required=True)
    acc.add_argument("--handle", required=True)
    acc.add_argument("--sub", required=True)
    acc.add_argument("--cap", type=int, default=3)
    acc.add_argument("--hue", type=int, default=20)
    acc.set_defaults(fn=cmd_add_account)

    seed = sub.add_parser("seed-products", help="ใส่คลังสินค้าให้ผู้ใช้ (ใช้ซ่อมตอนสร้างแล้วพลาด)")
    seed.add_argument("--email", required=True)
    seed.set_defaults(fn=cmd_seed_products)

    lst = sub.add_parser("list", help="ดูผู้ใช้ บัญชี และสถานะคิว")
    lst.set_defaults(fn=cmd_list)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
