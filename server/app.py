"""ClipQueue HTTP API + the web UI it serves.

The UI must be served from here, not opened as a file or published as an
artifact: a page on another origin cannot call this API (and a published
artifact's sandbox blocks the request outright).

    uvicorn server.app:app --reload --port 8787
"""

from __future__ import annotations

import json
import os
import time
from collections import defaultdict, deque
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import load_env

# Must run before anything reads os.environ: server.notify and server.outbox
# capture their settings at import time, so a late load leaves them unconfigured.
load_env()

from . import db, service, worker  # noqa: E402
from .models import (ASPECTS, FORMATS, MAX_SHOTS, MIN_SHOTS, CapRequest,  # noqa: E402
                     GenerateShotsRequest, QueueRequest)

ROOT = Path(__file__).resolve().parent.parent
APP_DIR = ROOT / "app"
START_WORKER = os.environ.get("CLIPQUEUE_WORKER", "1") == "1"

RATE_LIMIT = int(os.environ.get("CLIPQUEUE_RATE_LIMIT", "120"))  # requests/minute/token
_hits: dict[str, deque] = defaultdict(deque)

# Set when the app is reachable from the internet (a tunnel, a VPS). It is not
# a security feature by itself — it turns on the checks that only matter once
# strangers can reach the port.
PUBLIC = os.environ.get("CLIPQUEUE_PUBLIC") == "1"
MIN_TOKEN_LEN = 24

app = FastAPI(
    title="ClipQueue",
    version="0.7.0",
    # the schema browser is handy locally but hands a stranger the whole API
    # surface; off once the port is on the internet
    docs_url=None if PUBLIC else "/api/docs",
    openapi_url=None if PUBLIC else "/api/openapi.json",
)


@app.on_event("startup")
def _startup() -> None:
    db.init()
    if PUBLIC and os.environ.get("CLIPQUEUE_DEMO_TOKEN"):
        raise RuntimeError(
            "CLIPQUEUE_DEMO_TOKEN ถูกตั้งไว้พร้อมกับ CLIPQUEUE_PUBLIC=1\n"
            "โทเคนตัวอย่างเป็นค่าที่เดาได้ ห้ามใช้ตอนเปิดออกอินเทอร์เน็ต\n"
            "ลบ CLIPQUEUE_DEMO_TOKEN ออกจาก .env แล้วสร้างผู้ใช้จริงด้วย\n"
            "  python -m server.admin create-user --email ... --name ..."
        )
    seed_demo_user()
    if PUBLIC:
        print(f"โหมดสาธารณะ: เปิดอยู่ · จำกัด {RATE_LIMIT} คำขอ/นาที")
    if START_WORKER:
        worker.start_background()


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    # the page embeds nothing and is embedded nowhere; say so explicitly
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    # a clip URL must never end up in a shared cache
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.on_event("shutdown")
def _shutdown() -> None:
    worker.stop()


# ----------------------------------------------------------------- auth

def current_user(authorization: str = Header(default="")) -> dict:
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(401, "ต้องส่ง Authorization: Bearer <token>")
    if PUBLIC and len(token) < MIN_TOKEN_LEN:
        # short tokens are brute-forceable once the port is on the internet;
        # refuse them outright rather than let one through on a lucky guess
        raise HTTPException(401, "token สั้นเกินไปสำหรับโหมดสาธารณะ")
    row = db.user_by_token(token)
    if row is None:
        raise HTTPException(401, "token ไม่ถูกต้อง")
    return dict(row)


@app.middleware("http")
async def rate_limit(request: Request, call_next):
    """A crude per-token limiter. Enough to stop a runaway client; put a real
    one in the reverse proxy before this is public."""
    if request.url.path.startswith("/api/"):
        key = request.headers.get("authorization", request.client.host if request.client else "?")
        window = _hits[key]
        now = time.monotonic()
        while window and now - window[0] > 60:
            window.popleft()
        if len(window) >= RATE_LIMIT:
            return JSONResponse({"detail": "ยิงถี่เกินไป ลองใหม่ในอีกสักครู่"}, status_code=429)
        window.append(now)
    return await call_next(request)


@app.exception_handler(service.ServiceError)
async def _service_error(_: Request, exc: service.ServiceError):
    return JSONResponse({"detail": str(exc)}, status_code=exc.status)


# ------------------------------------------------------------- routes

@app.get("/api/health")
def health() -> dict:
    import shutil

    from src import aigen, scene as scenes, script_gen, style as styles, talk as talks

    from . import notify, outbox
    return {
        "ok": True,
        "styles": styles.choices(),
        "facts": [{"id": k, "name": v} for k, v in script_gen.FACTS.items()],
        "talks": talks.choices(),
        "scenes": scenes.choices(),
        "shotRange": [MIN_SHOTS, MAX_SHOTS],
        "hasClaudeKey": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "google": aigen.choices(),
        "ffmpeg": bool(shutil.which("ffmpeg")),
        "worker": START_WORKER,
        "fake_render": worker.FAKE,
        "outbox": str(outbox.OUTBOX) if outbox.enabled() else "",
        "notify": notify.describe(),
        "formats": FORMATS,
        "aspects": {k: {"w": v[0], "h": v[1]} for k, v in ASPECTS.items()},
    }


@app.get("/api/state")
def get_state(user=Depends(current_user)) -> dict:
    return {"user": {"name": user["name"], "email": user["email"]}, **service.state(user["id"])}


@app.post("/api/queue", status_code=201)
def post_queue(req: QueueRequest, user=Depends(current_user)) -> dict:
    return service.create_queue(user["id"], req)


@app.post("/api/jobs/{job_id}/posted")
def post_posted(job_id: str, user=Depends(current_user)) -> dict:
    if not db.mark_posted(user["id"], job_id):
        raise HTTPException(409, "ทำเครื่องหมายได้เฉพาะคลิปที่สถานะ 'พร้อมอัป'")
    db.log(user["id"], f"อัปขึ้น Shopee แล้ว · {job_id}", "ok", job_id)
    from . import outbox
    outbox.forget(job_id)  # posted clips do not need to stay on the phone
    return {"ok": True}


@app.get("/api/next")
def get_next(user=Depends(current_user)) -> dict:
    """The one clip to post right now, plus how many are behind it."""
    from . import outbox

    rows = db.connect().execute(
        "SELECT * FROM jobs WHERE user_id = ? AND status = 'ready'"
        " ORDER BY scheduled_at, created_at",
        (user["id"],),
    ).fetchall()
    if not rows:
        return {"waiting": 0, "clip": None}

    row = rows[0]
    view = service.job_view(user["id"], row)
    return {
        "waiting": len(rows),
        "clip": {
            "id": view["id"],
            "product": view["product"]["name"],
            "account": view["account"],
            "caption": view["caption"],
            "link": view["link"],
            "scheduledAt": view["scheduledAt"],
            "video": view["video"],
            "sizeKB": view["sizeKB"],
            "duration": view["duration"],
            "outboxName": (outbox.filename(row, view["product"]["name"], view["account"]) + ".mp4")
                          if outbox.enabled() else "",
        },
    }


@app.post("/api/jobs/{job_id}/retry")
def post_retry(job_id: str, user=Depends(current_user)) -> dict:
    if not db.requeue(user["id"], job_id):
        raise HTTPException(409, "ส่งกลับเข้าคิวได้เฉพาะคลิปที่ล้มเหลวหรือเรนเดอร์เสร็จแล้ว")
    db.log(user["id"], f"ส่งกลับเข้าคิว · {job_id}", "warn", job_id)
    return {"ok": True}


@app.get("/api/jobs/{job_id}/video")
def get_video(job_id: str, user=Depends(current_user)):
    row = db.job(user["id"], job_id)
    if row is None:
        raise HTTPException(404, "ไม่พบคลิปนี้")
    path = Path(row["video_path"])
    # the stored path is server-generated, but check anyway: a path from the
    # database should never be able to read outside the renders directory
    try:
        path.resolve().relative_to(worker.RENDERS.resolve())
    except ValueError:
        raise HTTPException(404, "ไฟล์อยู่นอกโฟลเดอร์ที่อนุญาต") from None
    if not path.exists():
        raise HTTPException(404, "ยังไม่มีไฟล์คลิป")
    return FileResponse(path, media_type="video/mp4", filename=f"{job_id}.mp4")


@app.put("/api/accounts/{account_id}/cap")
def put_cap(account_id: str, req: CapRequest, user=Depends(current_user)) -> dict:
    if not db.set_cap(user["id"], account_id, req.cap):
        raise HTTPException(404, "ไม่พบบัญชีนี้")
    db.log(user["id"], f"ตั้งเพดาน {account_id} เป็น {req.cap or 'ไม่จำกัด'} คลิป/วัน", "warn")
    return {"ok": True, "cap": req.cap}


@app.post("/api/personas/generate")
def post_generate_shots(req: GenerateShotsRequest, user=Depends(current_user)) -> dict:
    """สร้างช็อตคนด้วย AI แล้วเซฟลงโฟลเดอร์ตัวละคร

    เสียเงินจริงต่อภาพ จึงเรียกได้เฉพาะเมื่อผู้ใช้กดเอง ไม่มีการเรียกอัตโนมัติ
    ระหว่างเรนเดอร์
    """
    from src import aigen, scene as scenes

    product_name = ""
    if req.product_id:
        found = db.product(user["id"], req.product_id)
        if found is None:
            raise HTTPException(404, f"ไม่พบสินค้า {req.product_id} ในคลังของผู้ใช้นี้")
        product_name = found["name"]

    prompt = aigen.build_prompt(req.scene, req.look, product_name)
    try:
        images = aigen.generate_images(prompt, req.count)
        saved = aigen.save_shots(req.persona, req.scene, images)
    except aigen.GenError as exc:
        db.log(user["id"], f"สร้างช็อตคนด้วย AI ไม่สำเร็จ · {exc}", "error")
        raise HTTPException(502, str(exc)) from exc

    where = scenes.folder_name(req.scene) or "โฟลเดอร์หลัก"
    db.log(user["id"],
           f"สร้างช็อต {req.persona} ด้วย AI {len(saved)} รูป · {where}", "ok")
    return {
        "saved": [p.name for p in saved],
        "folder": where,
        "model": aigen.image_model(),
        "prompt": prompt,
    }


@app.get("/api/stats")
def get_stats(user=Depends(current_user)) -> dict:
    from . import stats
    return stats.summary(user["id"])


@app.post("/api/stats/sync")
def post_stats_sync(days: int = 14, user=Depends(current_user)) -> dict:
    from . import stats
    if not 1 <= days <= stats.MAX_DAYS:
        raise HTTPException(422, f"days ต้องอยู่ระหว่าง 1 ถึง {stats.MAX_DAYS}")
    try:
        return stats.sync(user["id"], days)
    except Exception as exc:  # noqa: BLE001 — surfaced to the operator as-is
        raise HTTPException(502, f"ดึงรายงานจาก Shopee ไม่สำเร็จ: {exc}") from exc


@app.delete("/api/events")
def delete_events(user=Depends(current_user)) -> dict:
    db.clear_events(user["id"])
    return {"ok": True}


# ---------------------------------------------------------------- seed

def seed_demo_user() -> None:
    """Create the demo account only when a token is handed in deliberately.

    This used to run on every boot with a hardcoded token, which meant any
    fresh deployment was reachable by anyone who guessed 'demo-token'. Now it
    is opt-in: tests and local playgrounds set CLIPQUEUE_DEMO_TOKEN, real
    installs create their user with `python -m server.admin create-user`.
    """
    token = os.environ.get("CLIPQUEUE_DEMO_TOKEN")
    if not token:
        if not db.connect().execute("SELECT 1 FROM users LIMIT 1").fetchone():
            print("ยังไม่มีผู้ใช้ในระบบ — สร้างด้วย:\n"
                  "  python -m server.admin create-user --email you@example.com --name \"ชื่อร้าน\"")
        return

    email = os.environ.get("CLIPQUEUE_DEMO_EMAIL", "demo@clipqueue.local")
    if db.user_by_email(email):
        return

    user_id, _ = db.create_user(email, "ร้านดีดีช้อป", token)
    for handle, sub, cap, hue in [
        ("@deedeeshop", "sub_main", 5, 20),
        ("@deedee.clips", "sub_clip", 4, 200),
        ("@ของถูกบอกต่อ", "sub_cheap", 4, 280),
    ]:
        db.upsert_account(user_id, handle, sub, cap, hue)

    # a real import (tools/fetch_products.py) wins; otherwise ship the demo set
    catalogue = APP_DIR / "products.json"
    if not catalogue.exists():
        catalogue = Path(__file__).resolve().parent / "seed_products.json"
    if catalogue.exists():
        db.replace_products(user_id, json.loads(catalogue.read_text(encoding="utf-8")))
    db.log(user_id, "สร้างผู้ใช้ตัวอย่างและบัญชี Shopee 3 บัญชี")
    print(f"ผู้ใช้ตัวอย่าง: {email} · token: {token}")


# ------------------------------------------------------------- the UI

@app.get("/")
def index():
    return FileResponse(APP_DIR / "clipqueue.html")


@app.get("/post")
def post_page():
    """Phone-sized page for the one manual step Shopee leaves us."""
    return FileResponse(APP_DIR / "post.html")


if APP_DIR.exists():
    app.mount("/app", StaticFiles(directory=APP_DIR), name="app")
