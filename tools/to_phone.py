"""ส่งคลิปที่พร้อมอัปเข้ามือถือ Android แล้วเปิดทางให้กดอัปได้เร็วที่สุด

สิ่งที่สคริปต์นี้ทำ ล้วนเป็นช่องทางที่ Android กับ Shopee เปิดไว้เอง — คัดลอกไฟล์
ลงเครื่อง, บอกระบบให้สแกนเข้าแกลเลอรี, และเรียกหน้า "แชร์" ของแอป
**ไม่มีการจำลองการกดปุ่มในแอป Shopee** ซึ่งเป็นสิ่งที่ผิดเงื่อนไขการใช้งาน

    python tools/to_phone.py                 # ส่งคลิปถัดไปที่รออัป
    python tools/to_phone.py --job j_xxx     # เจาะจงคลิป
    python tools/to_phone.py --share         # เปิดหน้าแชร์ของ Shopee ให้ด้วย
    python tools/to_phone.py --mirror        # เปิด scrcpy ต่อเลย
    python tools/to_phone.py --check         # แค่ตรวจว่าพร้อมไหม
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server import db  # noqa: E402

# โฟลเดอร์ใน shared storage ที่แกลเลอรีมองเห็น
PHONE_DIR = "/sdcard/Movies/ClipQueue"
SHOPEE_HINTS = ("shopee",)


def run(args: list[str], timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


def need(tool: str) -> str:
    path = shutil.which(tool)
    if not path:
        sys.exit(f"ไม่พบ {tool} ใน PATH\n"
                 f"  adb    : winget install Google.PlatformTools\n"
                 f"  scrcpy : winget install Genymobile.scrcpy\n"
                 f"ติดตั้งแล้วให้เปิด terminal ใหม่")
    return path


def devices() -> list[tuple[str, str]]:
    out = run([need("adb"), "devices"]).stdout.splitlines()[1:]
    found = []
    for line in out:
        parts = line.split()
        if len(parts) >= 2:
            found.append((parts[0], parts[1]))
    return found


def require_device() -> str:
    found = devices()
    ready = [d for d, state in found if state == "device"]
    if ready:
        return ready[0]

    unauthorised = [d for d, state in found if state == "unauthorized"]
    if unauthorised:
        sys.exit("มือถือต่ออยู่แต่ยังไม่ได้อนุญาต — ดูที่หน้าจอมือถือแล้วกด "
                 "'อนุญาตการแก้จุดบกพร่อง USB' (ติ๊ก 'เสมอ' ด้วยจะได้ไม่ถามอีก)")
    sys.exit(
        "ไม่พบมือถือ Android\n"
        "  1. เปิด ตั้งค่า > เกี่ยวกับโทรศัพท์ > กด 'หมายเลขบิลด์' 7 ครั้ง\n"
        "  2. ตั้งค่า > ตัวเลือกสำหรับนักพัฒนา > เปิด 'การแก้จุดบกพร่อง USB'\n"
        "  3. เสียบสาย USB แล้วเลือกโหมด 'ถ่ายโอนไฟล์'\n"
        "  4. รันใหม่อีกครั้ง"
    )


def shopee_package(serial: str) -> str | None:
    out = run([need("adb"), "-s", serial, "shell", "pm", "list", "packages"]).stdout
    for line in out.splitlines():
        name = line.replace("package:", "").strip()
        if any(h in name.lower() for h in SHOPEE_HINTS):
            return name
    return None


def next_clip(user_id: str, job_id: str | None):
    if job_id:
        row = db.job(user_id, job_id)
        if row is None:
            sys.exit(f"ไม่พบคลิป {job_id}")
        if not row["video_path"]:
            sys.exit(f"คลิป {job_id} ยังไม่มีไฟล์ (สถานะ {row['status']})")
        return row

    rows = db.connect().execute(
        "SELECT * FROM jobs WHERE user_id = ? AND status = 'ready' AND video_path != ''"
        " ORDER BY scheduled_at, created_at LIMIT 1",
        (user_id,),
    ).fetchall()
    if not rows:
        sys.exit("ไม่มีคลิปที่รออัป — เรนเดอร์ก่อนด้วย ClipQueue")
    return rows[0]


def only_user():
    rows = db.connect().execute("SELECT * FROM users ORDER BY created_at").fetchall()
    if not rows:
        sys.exit("ยังไม่มีผู้ใช้ — สร้างด้วย python -m server.admin create-user")
    if len(rows) > 1:
        sys.exit("มีผู้ใช้หลายคน ระบุด้วย --email")
    return rows[0]


def copy_to_pc_clipboard(text: str) -> bool:
    """วางแคปชันไว้ในคลิปบอร์ดของคอม — scrcpy ซิงก์คลิปบอร์ดสองทาง
    กด Ctrl+V ในมือถือผ่าน scrcpy ได้เลยโดยไม่ต้องพิมพ์"""
    try:
        proc = subprocess.run(["clip"], input=text.encode("utf-16-le"), check=True)
        return proc.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="ส่งคลิปเข้ามือถือ Android")
    ap.add_argument("--job", help="รหัสคลิป (ไม่ใส่ = ตัวถัดไปที่รออัป)")
    ap.add_argument("--email", help="ระบุผู้ใช้เมื่อมีหลายคน")
    ap.add_argument("--share", action="store_true", help="เปิดหน้าแชร์ของ Shopee ต่อเลย")
    ap.add_argument("--mirror", action="store_true", help="เปิด scrcpy ต่อเลย")
    ap.add_argument("--check", action="store_true", help="ตรวจความพร้อมอย่างเดียว")
    args = ap.parse_args(argv)

    adb = need("adb")

    if args.check:
        print(f"adb    : {adb}")
        print(f"scrcpy : {shutil.which('scrcpy') or 'ยังไม่ได้ติดตั้ง'}")
        found = devices()
        print(f"มือถือ : {found or 'ไม่พบ'}")
        if found:
            serial = require_device()
            model = run([adb, "-s", serial, "shell", "getprop", "ro.product.model"]).stdout.strip()
            sdk = run([adb, "-s", serial, "shell", "getprop", "ro.build.version.sdk"]).stdout.strip()
            print(f"รุ่น    : {model} · Android SDK {sdk}")
            print(f"Shopee : {shopee_package(serial) or 'ไม่พบแอป Shopee'}")
        return 0

    db.init()
    user = db.user_by_email(args.email) if args.email else only_user()
    job = next_clip(user["id"], args.job)
    local = Path(job["video_path"])
    if not local.exists():
        sys.exit(f"ไม่พบไฟล์ {local} — อาจถูกลบด้วย tools/prune.py ไปแล้ว")

    product = db.product(user["id"], job["product_id"]) or {"name": job["product_id"]}
    serial = require_device()

    from server import outbox
    account = db.connect().execute(
        "SELECT handle FROM accounts WHERE id = ?", (job["account_id"],)
    ).fetchone()
    handle = account["handle"] if account else "unknown"
    name = outbox.filename(job, product["name"], handle) + ".mp4"
    remote = f"{PHONE_DIR}/{name}"

    print(f"{product['name']}  →  {handle}")
    run([adb, "-s", serial, "shell", "mkdir", "-p", PHONE_DIR])
    push = run([adb, "-s", serial, "push", str(local), remote], timeout=300)
    if push.returncode != 0:
        sys.exit(f"ส่งไฟล์ไม่สำเร็จ: {push.stderr.strip()[:200]}")
    print(f"  ส่งไฟล์แล้ว  {remote}")

    # บอกระบบให้สแกนเข้าแกลเลอรี — Android 10 ขึ้นไปอาจไม่สนใจ broadcast นี้
    # แต่ไฟล์ก็ยังเปิดได้จากแอปจัดการไฟล์อยู่ดี จึงไม่ถือว่าล้มเหลว
    scan = run([adb, "-s", serial, "shell", "am", "broadcast",
                "-a", "android.intent.action.MEDIA_SCANNER_SCAN_FILE",
                "-d", f"file://{remote}"])
    print("  แกลเลอรี   " + ("สแกนแล้ว" if scan.returncode == 0 else
                             "สแกนอัตโนมัติไม่ได้ (เปิดจากแอปไฟล์ได้)"))

    if copy_to_pc_clipboard(job["caption"]):
        print("  แคปชัน     อยู่ในคลิปบอร์ดคอมแล้ว — ใน scrcpy กด Ctrl+V วางได้เลย")
    else:
        print("  แคปชัน     คัดลอกอัตโนมัติไม่ได้ ดูด้านล่าง")

    pkg = shopee_package(serial)
    if args.share:
        if not pkg:
            print("  แชร์       ไม่พบแอป Shopee ในเครื่องนี้")
        else:
            # ส่งไฟล์ให้ Shopee ผ่าน ACTION_SEND ซึ่งเป็นหน้าแชร์ที่แอปเปิดรับเอง
            # ถ้า Shopee ไม่ได้ลงทะเบียนรับ video/mp4 ไว้ คำสั่งนี้จะไม่มีอะไรเกิดขึ้น
            send = run([adb, "-s", serial, "shell", "am", "start",
                        "-a", "android.intent.action.SEND", "-t", "video/mp4",
                        "--eu", "android.intent.extra.STREAM", f"file://{remote}",
                        "-p", pkg])
            ok = send.returncode == 0 and "Error" not in send.stdout
            print(f"  แชร์       {'เปิดหน้าแชร์ของ ' + pkg if ok else 'Shopee ไม่รับไฟล์ทางนี้ — เปิดแอปเองแทน'}")
    elif pkg:
        print(f"  แอป Shopee {pkg}")

    print("\nแคปชัน")
    print("─" * 56)
    print(job["caption"])
    print("─" * 56)

    if args.mirror:
        need("scrcpy")
        print("\nเปิด scrcpy — คุมมือถือด้วยเมาส์และคีย์บอร์ด ปิดหน้าต่างเพื่อออก")
        subprocess.run(["scrcpy", "-s", serial, "--window-title", f"ClipQueue · {handle}"])
    else:
        print("\nอยากคุมมือถือจากคอม: python tools/to_phone.py --mirror")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
