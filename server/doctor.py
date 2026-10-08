"""Pre-flight check: everything that silently breaks a render, checked up front.

    python -m server.doctor

The Thai font check is the one that earns its keep — without a Thai font
libass draws captions as empty boxes and ffmpeg reports success anyway.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .config import load_env

load_env()

ROOT = Path(__file__).resolve().parent.parent

OK, WARN, BAD = "ok  ", "เตือน", "พัง "
problems = 0
warnings = 0


def line(state: str, label: str, detail: str = "") -> None:
    global problems, warnings
    if state == BAD:
        problems += 1
    if state == WARN:
        warnings += 1
    print(f"  {state}  {label}" + (f"  —  {detail}" if detail else ""))


def check_ffmpeg() -> None:
    exe = shutil.which("ffmpeg")
    if not exe:
        line(BAD, "ffmpeg", "ไม่พบใน PATH · winget install Gyan.FFmpeg")
        return
    out = subprocess.run([exe, "-version"], capture_output=True, text=True).stdout
    line(OK, "ffmpeg", out.splitlines()[0].split(" Copyright")[0])

    filters = subprocess.run([exe, "-hide_banner", "-filters"],
                             capture_output=True, text=True).stdout
    for name, why in [("ass", "เบิร์นซับ"), ("zoompan", "ซูมภาพ"),
                      ("silenceremove", "ตัดความเงียบ"), ("adelay", "วางเสียงบนไทม์ไลน์")]:
        if f" {name} " in filters:
            line(OK, f"ฟิลเตอร์ {name}", why)
        else:
            line(BAD, f"ฟิลเตอร์ {name}", f"ขาดไม่ได้ ({why}) — ใช้ ffmpeg build แบบ full")


def check_thai_font() -> None:
    """Render one Thai glyph and see whether anything was actually drawn."""
    exe = shutil.which("ffmpeg")
    if not exe:
        line(WARN, "ฟอนต์ไทย", "ข้ามเพราะไม่มี ffmpeg")
        return

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        ass = work / "t.ass"
        ass.write_text(
            "[Script Info]\nScriptType: v4.00+\nPlayResX: 320\nPlayResY: 240\n\n"
            "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, Alignment\n"
            "Style: D,Leelawadee UI,72,&H00FFFFFF,5\n\n"
            "[Events]\nFormat: Layer, Start, End, Style, Text\n"
            "Dialogue: 0,0:00:00.00,0:00:01.00,D,ทดสอบภาษาไทย\n",
            encoding="utf-8-sig",
        )
        proc = subprocess.run(
            [exe, "-hide_banner", "-loglevel", "error", "-y",
             "-f", "lavfi", "-i", "color=c=black:s=320x240:d=1",
             "-vf", "ass=t.ass", "-frames:v", "1", "out.png"],
            cwd=work, capture_output=True, text=True,
        )
        png = work / "out.png"
        if proc.returncode != 0 or not png.exists():
            line(BAD, "ฟอนต์ไทย", proc.stderr.strip()[:120] or "เรนเดอร์ทดสอบไม่ผ่าน")
            return
        # a frame with no glyphs drawn stays pure black and compresses tiny
        size = png.stat().st_size
        if size < 1200:
            line(BAD, "ฟอนต์ไทย",
                 f"ซับว่างเปล่า ({size} bytes) — ติดตั้งฟอนต์ไทย ไม่งั้นซับจะเป็นช่องว่าง")
        else:
            line(OK, "ฟอนต์ไทย", f"วาดตัวอักษรได้ ({size} bytes)")


def check_python_deps() -> None:
    for module, why in [("edge_tts", "เสียงพากย์"), ("PIL", "ประมวลผลรูป"),
                        ("requests", "เรียก Shopee API"), ("fastapi", "เว็บเซิร์ฟเวอร์"),
                        ("pydantic", "ตรวจข้อมูลขาเข้า")]:
        try:
            __import__(module)
            line(OK, f"แพ็กเกจ {module}", why)
        except ImportError:
            line(BAD, f"แพ็กเกจ {module}", f"ขาด ({why}) — pip install -r requirements.txt")

    try:
        __import__("anthropic")
        line(OK, "แพ็กเกจ anthropic", "ให้ Claude เขียนจุดขาย")
    except ImportError:
        line(WARN, "แพ็กเกจ anthropic", "ไม่มีก็ได้ แต่จะเขียนจุดขายด้วย Claude ไม่ได้")


def check_storage() -> None:
    from . import db, worker

    data = db.DB_PATH.parent
    data.mkdir(parents=True, exist_ok=True)
    try:
        probe = data / ".write-test"
        probe.write_text("x", encoding="utf-8")
        probe.unlink()
        line(OK, "เขียนโฟลเดอร์ data ได้", str(data))
    except OSError as exc:
        line(BAD, "เขียนโฟลเดอร์ data ไม่ได้", str(exc))
        return

    try:
        db.init()
        users = db.connect().execute("SELECT COUNT(*) n FROM users").fetchone()["n"]
        jobs = db.connect().execute("SELECT COUNT(*) n FROM jobs").fetchone()["n"]
        size = db.DB_PATH.stat().st_size / 1024 if db.DB_PATH.exists() else 0
        line(OK, "ฐานข้อมูล", f"ผู้ใช้ {users} · งาน {jobs} · {size:.0f} KB")
        if users == 0:
            line(WARN, "ยังไม่มีผู้ใช้",
                 "python -m server.admin create-user --email you@example.com --name \"ร้าน\"")
    except Exception as exc:  # noqa: BLE001
        line(BAD, "ฐานข้อมูล", str(exc))

    free = shutil.disk_usage(data).free / 1e9
    renders = worker.RENDERS
    used = sum(f.stat().st_size for f in renders.glob("*.mp4")) / 1e6 if renders.exists() else 0
    detail = f"เหลือ {free:.1f} GB · คลิปที่เก็บไว้ {used:.0f} MB"
    if free < 2:
        line(BAD, "พื้นที่ดิสก์", detail + " — เรนเดอร์จะล้มเหลว")
    elif free < 10:
        line(WARN, "พื้นที่ดิสก์", detail + " — ควรลบคลิปเก่า (tools/prune.py)")
    else:
        line(OK, "พื้นที่ดิสก์", detail)


def check_secrets() -> None:
    from src import shopee
    shopee.load_env()

    if os.environ.get("SHOPEE_APP_ID") and os.environ.get("SHOPEE_APP_SECRET"):
        line(OK, "คีย์ Shopee", "พร้อมดึงสินค้าจริง")
    else:
        line(WARN, "คีย์ Shopee", "ยังไม่ได้ตั้ง — ดึงสินค้าได้เฉพาะ --mock")

    if os.environ.get("ANTHROPIC_API_KEY"):
        line(OK, "คีย์ Anthropic", "ให้ Claude เขียนจุดขายได้")
    else:
        line(WARN, "คีย์ Anthropic", "จุดขายจะใช้ยอดขาย/เรตติ้งแทน")

    from src import aigen
    g = aigen.status()
    if g["unknownModels"]:
        line(WARN, "โมเดล Google", "ไม่รู้จักชื่อรุ่นใน " + ", ".join(g["unknownModels"])
             + " — ถอยไปใช้ค่าตั้งต้น")
    elif g["hasKey"]:
        line(OK, "คีย์ Google", f"ภาพ {g['imageModel']} · วิดีโอ {g['videoModel']}")
    else:
        line(WARN, "คีย์ Google", "ยังไม่ได้ตั้ง — สร้างช็อตคนด้วย AI ไม่ได้")

    if os.environ.get("CLIPQUEUE_DEMO_TOKEN"):
        line(WARN, "CLIPQUEUE_DEMO_TOKEN ถูกตั้งไว้",
             "เซิร์ฟเวอร์จะสร้างผู้ใช้ตัวอย่าง — ปลดออกก่อนใช้จริง")


def check_photos() -> None:
    folder = ROOT / "app" / "assets" / "products"
    if not folder.is_dir():
        line(WARN, "รูปสินค้า", "ยังไม่มี — รัน tools/fetch_products.py ไม่งั้นเรนเดอร์จะล้มเหลว")
        return
    dirs = [d for d in folder.iterdir() if d.is_dir() and list(d.glob("[0-9][0-9].jpg"))]
    if dirs:
        line(OK, "รูปสินค้า", f"{len(dirs)} สินค้ามีรูปพร้อมใช้")
    else:
        line(WARN, "รูปสินค้า", "โฟลเดอร์ว่าง — รัน tools/fetch_products.py")


def check_handoff() -> None:
    """The semi-automatic path: synced folder, notification, reachable URL."""
    from . import notify, outbox

    if not outbox.enabled():
        line(WARN, "โฟลเดอร์ส่งคลิปเข้ามือถือ",
             "ยังไม่ได้ตั้ง CLIPQUEUE_OUTBOX — ต้องดาวน์โหลดคลิปเอง")
    else:
        folder = outbox.OUTBOX
        try:
            folder.mkdir(parents=True, exist_ok=True)
            probe = folder / ".write-test"
            probe.write_text("x", encoding="utf-8")
            probe.unlink()
            waiting = len(list(folder.glob("*.mp4")))
            line(OK, "โฟลเดอร์ส่งคลิปเข้ามือถือ", f"{folder} · มีคลิปอยู่ {waiting}")
        except OSError as exc:
            line(BAD, "โฟลเดอร์ส่งคลิปเข้ามือถือ", f"เขียนไม่ได้: {exc}")

    state = notify.describe()
    if not notify.enabled():
        line(WARN, "แจ้งเตือนเข้ามือถือ", "ปิดอยู่ — ตั้ง CLIPQUEUE_NOTIFY ใน .env")
    elif "พร้อมใช้" in state:
        line(OK, "แจ้งเตือนเข้ามือถือ", state)
    else:
        line(BAD, "แจ้งเตือนเข้ามือถือ", state)

    base = notify.BASE_URL
    if "127.0.0.1" in base or "localhost" in base:
        line(WARN, "ลิงก์ในแจ้งเตือน",
             f"{base} — มือถือเปิดไม่ได้ ใส่ IP ของเครื่องนี้ใน CLIPQUEUE_BASE_URL")
    else:
        line(OK, "ลิงก์ในแจ้งเตือน", f"{base}/post")


def main() -> int:
    print("\nตรวจความพร้อม ClipQueue\n")
    print("เรนเดอร์")
    check_ffmpeg()
    check_thai_font()
    print("\nแพ็กเกจ")
    check_python_deps()
    print("\nข้อมูล")
    check_storage()
    check_photos()
    print("\nส่งคลิปเข้ามือถือ")
    check_handoff()
    print("\nคีย์และความปลอดภัย")
    check_secrets()

    print()
    if problems:
        print(f"มี {problems} เรื่องที่ต้องแก้ก่อนใช้งาน"
              + (f" · อีก {warnings} เรื่องควรดู" if warnings else ""))
        return 1
    if warnings:
        print(f"ใช้งานได้ · มี {warnings} เรื่องควรดู")
        return 0
    print("พร้อมใช้งานทุกอย่าง")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
