"""Render worker: claims queued jobs and drives the real ffmpeg pipeline.

Runs as a thread inside the API process by default, or standalone with
`python -m server.worker` on its own machine — the claim is atomic either way,
so you can run several.

Set CLIPQUEUE_FAKE_RENDER=1 to exercise the queue without ffmpeg (tests, CI).
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import tempfile
import threading
import time
import traceback
from pathlib import Path

from .config import load_env

load_env()

from . import db  # noqa: E402
from .models import ASPECTS, FORMATS  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
RENDERS = Path(os.environ.get("CLIPQUEUE_RENDERS", ROOT / "data" / "renders"))
POLL_SECONDS = float(os.environ.get("CLIPQUEUE_POLL", "2"))
FAKE = os.environ.get("CLIPQUEUE_FAKE_RENDER") == "1"

_stop = threading.Event()


def caption_for(product: dict, link: str, cart: bool, style=None) -> str:
    tags = ["#ShopeeTH", "#ตะกร้าส้ม",
            "#ส่งฟรี" if product.get("free") else "#ของมันต้องมี"]
    # แท็กของแนวมาก่อน เพื่อให้คลิปแต่ละแนวไปโผล่ในฟีดคนละกลุ่ม
    tags = [*(getattr(style, "tags", ()) or ("#ของดีบอกต่อ",)), *tags]
    head = f"{product['name']} เหลือ {product['price']:,} บาท"
    if product.get("was"):
        head += f" (ปกติ {product['was']:,})"
    body = product["bullets"][0] if product.get("bullets") else ""
    out = f"{head}\n{body}\n"
    if cart and link:
        out += f"\nสั่งได้ที่ {link}\n"
    return out + " ".join(tags)


def photos_for(product_id: str, count: int) -> list[Path]:
    folder = ROOT / "app" / "assets" / "products" / str(product_id)
    if not folder.is_dir():
        return []
    return sorted(folder.glob("[0-9][0-9].jpg"))[:count]


def script_for(product: dict, job, style, facts, mode: str) -> list[str]:
    """สคริปต์ของงานนี้ — ให้ Claude เขียนถ้าขอไว้และมีคีย์

    ไม่มีคีย์แล้วล้มทั้งงานไม่คุ้ม เพราะเทมเพลตให้ผลที่ใช้ได้อยู่แล้ว
    บันทึกไว้ใน log ว่าถอยมาใช้อะไร จะได้ไม่งงว่าทำไมสำนวนไม่เหมือนที่สั่ง
    """
    from src import script_gen

    if mode != "llm":
        return script_gen.plan_lines(product, job["format"], seed=None,
                                     style=style, facts=facts)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        db.log(job["user_id"], "ขอให้ Claude เขียนสคริปต์ แต่ยังไม่ได้ตั้ง"
               " ANTHROPIC_API_KEY — ใช้เทมเพลตแทน", "warn", job["id"])
        return script_gen.plan_lines(product, job["format"], seed=None,
                                     style=style, facts=facts)
    try:
        text = script_gen.build_llm_script(product, job["format"], style, facts)
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if not lines:
            raise RuntimeError("โมเดลตอบกลับมาว่าง")
        db.log(job["user_id"], f"Claude เขียนสคริปต์ให้ {len(lines)} บรรทัด",
               "ok", job["id"])
        return lines
    except Exception as exc:  # noqa: BLE001 — เน็ตล่มไม่ควรทำให้คลิปไม่ได้เรนเดอร์
        db.log(job["user_id"], f"Claude เขียนสคริปต์ไม่สำเร็จ · {exc} — ใช้เทมเพลตแทน",
               "warn", job["id"])
        return script_gen.plan_lines(product, job["format"], seed=None,
                                     style=style, facts=facts)


def render(job) -> dict:
    """Run the pipeline for one job. Returns the fields to store."""
    from src import (images, persona, render as renderer, script_gen, speech,
                     style as styles, subtitle, voice)

    user_id = job["user_id"]
    product = db.product(user_id, job["product_id"])
    if product is None:
        raise RuntimeError("สินค้าถูกลบไปแล้วระหว่างรอคิว")

    keys = job.keys()
    style = styles.get(job["style"] if "style" in keys else None)
    # ว่าง = ครบทุกชิ้น ซึ่งเป็นพฤติกรรมเดิมของงานที่สร้างก่อนมีตัวเลือกนี้
    raw_facts = (job["facts"] if "facts" in keys else "") or ""
    facts = [f for f in raw_facts.split(",") if f] or None
    shot_count = (job["shot_count"] if "shot_count" in keys else 0) or product.get("shots", 4)
    mode = (job["script_mode"] if "script_mode" in keys else "template") or "template"

    lines = script_for(product, job, style, facts, mode)
    db.set_progress(job["id"], 10)

    if FAKE:
        # no ffmpeg, no network — enough to prove the queue and the handoff
        time.sleep(0.05)
        db.set_progress(job["id"], 60)
        out = RENDERS / f"{job['id']}.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"fake-mp4")
        return {
            "script": lines,
            "caption": caption_for(product, job["link"], bool(job["cart"]), style),
            "duration": speech.estimate(lines), "size_kb": 1, "video_path": str(out),
        }

    w, h = ASPECTS[job["aspect"]]
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        audio, cues, total = voice.synthesize("\n".join(lines), work, job["voice"])
        db.set_progress(job["id"], 45)

        photos = photos_for(product["id"], shot_count)
        if not photos:
            raise RuntimeError(
                "ไม่มีรูปสินค้า — รัน tools/fetch_products.py เพื่อดึงรูปก่อน"
            )
        # prepare คืนช็อตครบตามจำนวนที่ขอเสมอ รูปน้อยกว่าก็วนซ้ำให้
        if shot_count > len(photos):
            db.log(job["user_id"],
                   f"ขอ {shot_count} ช็อต แต่มีรูปสินค้า {len(photos)} รูป — วนรูปซ้ำ",
                   "warn", job["id"])
        shots = images.prepare(photos, work / "shots", shot_count) or photos

        # ช็อตคนจาก app/assets/personas/<ชื่อตัวละคร>/ — ไม่มีก็ใช้สินค้าล้วน
        people = persona.shots(job["persona"])
        if people and style.belt:
            # สายพานคือภาพไหลต่อเนื่อง ไม่มีช็อต จึงไม่มีที่ให้แทรกคน
            db.log(job["user_id"],
                   f"แนว{style.name}ใช้ภาพสินค้าล้วน ข้ามช็อต {job['persona']}",
                   "warn", job["id"])
            people = []
        elif people:
            shots = persona.interleave(shots, people, share=style.person_share)
            used = [s for s in shots if s in people]
            moving = sum(1 for s in used if persona.is_video(s))
            db.log(job["user_id"],
                   f"ใส่ช็อต {job['persona']} {len(used)}/{len(shots)} ช็อต"
                   f" (วิดีโอ {moving})", "ok", job["id"])

        ass = subtitle.write_ass(work / "captions.ass", cues, product, total, w, h)
        db.set_progress(job["id"], 60)
        db.log(job["user_id"], f"แนว{style.name}", "info", job["id"])
        visual = renderer.build_visual(shots, total, work, w, h,
                                       people=set(people), style=style)
        db.set_progress(job["id"], 85)

        RENDERS.mkdir(parents=True, exist_ok=True)
        out = RENDERS / f"{job['id']}.mp4"
        renderer.mux(visual, audio.resolve(), ass, out)

    return {
        "script": lines,
        "caption": caption_for(product, job["link"], bool(job["cart"]), style),
        "duration": total,
        "size_kb": round(out.stat().st_size / 1024),
        "video_path": str(out),
    }


def run_once() -> bool:
    """Claim and render one job. Returns False when the queue is empty."""
    db.reclaim_stale()
    job = db.claim_next()
    if job is None:
        return False

    db.log(job["user_id"], f"เริ่มเรนเดอร์ {job['id']}", "render", job["id"])
    try:
        result = render(job)
    except Exception as exc:  # noqa: BLE001 — a bad job must not kill the worker
        detail = f"{type(exc).__name__}: {exc}"
        db.fail_job(job["id"], detail)
        db.log(job["user_id"], f"เรนเดอร์ล้มเหลว {job['id']} · {detail}", "error", job["id"])
        if os.environ.get("CLIPQUEUE_DEBUG"):
            traceback.print_exc()
        return True

    db.finish_job(job["id"], **result)
    db.log(job["user_id"],
           f"คลิปพร้อม {job['id']} · {result['duration']:.1f} วิ · {result['size_kb']} KB",
           "ok", job["id"])
    handoff(job, result)
    return True


def handoff(job, result: dict) -> None:
    """Get the finished clip in front of the operator: into the synced folder
    and onto their phone. Neither step may fail the render that just succeeded."""
    from . import notify, outbox

    product = db.product(job["user_id"], job["product_id"]) or {"name": job["product_id"]}
    account = db.connect().execute(
        "SELECT handle FROM accounts WHERE id = ?", (job["account_id"],)
    ).fetchone()
    handle = account["handle"] if account else "(บัญชีถูกลบ)"

    try:
        target = outbox.publish(job, Path(result["video_path"]), result["caption"],
                                product["name"], handle)
        if target:
            db.log(job["user_id"], f"ส่งเข้าโฟลเดอร์ซิงก์ · {target.name}", "ok", job["id"])
    except Exception as exc:  # noqa: BLE001
        db.log(job["user_id"], f"คัดลอกเข้าโฟลเดอร์ซิงก์ไม่สำเร็จ · {exc}", "warn", job["id"])

    if notify.enabled():
        waiting = db.connect().execute(
            "SELECT COUNT(*) n FROM jobs WHERE user_id = ? AND status = 'ready'",
            (job["user_id"],),
        ).fetchone()["n"]
        first_line = (result["caption"] or "").splitlines()[0] if result["caption"] else ""
        if not notify.clip_ready(product["name"], handle, first_line, waiting):
            db.log(job["user_id"], "ส่งแจ้งเตือนไม่สำเร็จ", "warn", job["id"])


# A fresh process means no render of ours is actually running, so jobs left at
# 'rendering' are orphans — from a crash, a restart, or uvicorn --reload firing
# mid-render. Safe on a single worker; leave it off when several workers share
# one database, or a starting worker would steal a job another one is running.
RECLAIM_ON_START = os.environ.get("CLIPQUEUE_RECLAIM_ON_START", "1") == "1"


def loop() -> None:
    db.init()
    if RECLAIM_ON_START:
        rows = db.connect().execute(
            "SELECT id FROM jobs WHERE status = 'rendering'").fetchall()
        for row in rows:
            db.connect().execute(
                "UPDATE jobs SET status='queued', progress=0,"
                " error='เซิร์ฟเวอร์รีสตาร์ตระหว่างเรนเดอร์ ส่งกลับเข้าคิว' WHERE id=?",
                (row["id"],),
            )
        if rows:
            print(f"ส่งงานที่ค้างอยู่กลับเข้าคิว {len(rows)} ชิ้น")
    while not _stop.is_set():
        try:
            busy = run_once()
        except Exception:  # noqa: BLE001 — keep the worker alive through db hiccups
            traceback.print_exc()
            busy = False
        if not busy:
            _stop.wait(POLL_SECONDS)
    db.close()


def start_background() -> threading.Thread:
    thread = threading.Thread(target=loop, name="clipqueue-worker", daemon=True)
    thread.start()
    return thread


def stop() -> None:
    _stop.set()


if __name__ == "__main__":
    signal.signal(signal.SIGINT, lambda *_: stop())
    print(f"worker เริ่มทำงาน · db={db.DB_PATH} · fake={FAKE}")
    loop()
