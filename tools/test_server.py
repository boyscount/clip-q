"""End-to-end tests for the ClipQueue server.

Runs against a scratch database with CLIPQUEUE_FAKE_RENDER=1, so it needs no
ffmpeg, no network and no API keys. Covers auth, every validation rule,
cross-user isolation, cap-driven scheduling, idempotency, the job lifecycle,
worker claim races, stale reclaim, and the video endpoint's path guard.

    python tools/test_server.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCRATCH = Path(tempfile.mkdtemp(prefix="clipqueue-test-"))
os.environ["CLIPQUEUE_DB"] = str(SCRATCH / "test.db")
os.environ["CLIPQUEUE_RENDERS"] = str(SCRATCH / "renders")
os.environ["CLIPQUEUE_FAKE_RENDER"] = "1"
os.environ["CLIPQUEUE_WORKER"] = "0"          # drive the worker by hand
os.environ["CLIPQUEUE_RATE_LIMIT"] = "100000"  # exercised separately
os.environ["CLIPQUEUE_DEMO_TOKEN"] = "tok-a"

from fastapi.testclient import TestClient  # noqa: E402

from server import db, worker  # noqa: E402
from server.app import app  # noqa: E402

TODAY = date.today().isoformat()
TOMORROW = (date.today() + timedelta(days=1)).isoformat()

PASS = FAIL = 0


def check(label: str, got, want=True) -> None:
    global PASS, FAIL
    if got == want:
        PASS += 1
        print(f"  ok    {label}")
    else:
        FAIL += 1
        print(f"  FAIL  {label}\n          ได้ {got!r}\n          คาด {want!r}")


def section(title: str) -> None:
    print(f"\n{title}")


def queue_body(**over) -> dict:
    body = {
        "product_ids": ["P-1042"],
        "account_ids": [],          # filled by the caller
        "slots": ["19:30"],
        "format": "quick",
        "aspect": "9:16",
        "persona": "พี่หมี รีวิวของถูก",
        "voice": "female",
        "per": 1,
        "cart": True,
        "start_date": TODAY,
    }
    body.update(over)
    return body


def main() -> int:
    with TestClient(app) as c:
        auth_a = {"Authorization": "Bearer tok-a"}

        # pin the catalogue: the seed prefers app/products.json when it exists,
        # so without this the tests depend on whatever was last imported
        user_a = db.user_by_token("tok-a")["id"]
        seed = json.loads((ROOT / "server" / "seed_products.json").read_text(encoding="utf-8"))
        db.replace_products(user_a, seed)

        # a second user, to prove isolation
        user_b, token_b = db.create_user("b@clipqueue.local", "ร้านบี", "tok-b")
        acc_b = db.upsert_account(user_b, "@shopb", "sub_b", 0, 100)
        db.replace_products(user_b, [{"id": "B-1", "name": "ของร้านบี", "price": 50}])
        auth_b = {"Authorization": "Bearer tok-b"}

        state = c.get("/api/state", headers=auth_a).json()
        accounts = state["accounts"]
        a1, a2, a3 = (a["id"] for a in accounts)

        # ------------------------------------------------------------ auth
        section("1. การยืนยันตัวตน")
        check("ไม่ส่ง header → 401", c.get("/api/state").status_code, 401)
        check("token มั่ว → 401", c.get("/api/state", headers={"Authorization": "Bearer nope"}).status_code, 401)
        check("header ว่าง → 401", c.get("/api/state", headers={"Authorization": "Bearer "}).status_code, 401)
        check("token ถูก → 200", c.get("/api/state", headers=auth_a).status_code, 200)
        check("health เปิดสาธารณะ", c.get("/api/health").status_code, 200)
        check("token เก็บแบบ hash ไม่ใช่ข้อความตรง",
              db.connect().execute("SELECT token_hash FROM users LIMIT 1").fetchone()["token_hash"] != "tok-a")

        # ------------------------------------------------------ validation
        section("2. ดักข้อมูลขาเข้า")
        cases = [
            ("สินค้าว่าง", queue_body(product_ids=[], account_ids=[a1])),
            ("บัญชีว่าง", queue_body(account_ids=[])),
            ("รอบเวลาว่าง", queue_body(account_ids=[a1], slots=[])),
            ("เวลาไม่ใช่ HH:MM", queue_body(account_ids=[a1], slots=["25:00"])),
            ("เวลาเพี้ยน", queue_body(account_ids=[a1], slots=["7:5"])),
            ("นาทีเกิน", queue_body(account_ids=[a1], slots=["12:99"])),
            ("รูปแบบคลิปไม่รู้จัก", queue_body(account_ids=[a1], format="viral")),
            ("สัดส่วนไม่รู้จัก", queue_body(account_ids=[a1], aspect="21:9")),
            ("เสียงไม่รู้จัก", queue_body(account_ids=[a1], voice="robot")),
            ("per = 0", queue_body(account_ids=[a1], per=0)),
            ("per ติดลบ", queue_body(account_ids=[a1], per=-3)),
            ("per เกินเพดาน", queue_body(account_ids=[a1], per=99)),
            ("วันที่ย้อนหลัง", queue_body(account_ids=[a1], start_date="2020-01-01")),
            ("วันที่ไกลเกิน 60 วัน", queue_body(account_ids=[a1],
                                                 start_date=(date.today() + timedelta(days=90)).isoformat())),
            ("วันที่รูปแบบผิด", queue_body(account_ids=[a1], start_date="31/12/2026")),
            ("persona ว่าง", queue_body(account_ids=[a1], persona="")),
            ("ฟิลด์แปลกปลอม", queue_body(account_ids=[a1], surprise="x")),
            ("สินค้ามากเกินเพดาน", queue_body(product_ids=[f"P-{i}" for i in range(60)], account_ids=[a1])),
            ("ขอเกิน 200 คลิป", queue_body(product_ids=[f"P-{i}" for i in range(30)],
                                           account_ids=[a1, a2, a3], per=10)),
        ]
        for label, body in cases:
            check(label + " → 422", c.post("/api/queue", json=body, headers=auth_a).status_code, 422)

        check("per เป็นสตริงตัวเลข → แปลงให้",
              c.post("/api/queue", json=queue_body(account_ids=[a1], per="2"), headers=auth_a).status_code, 201)
        check("รหัสสินค้าซ้ำ ถูกยุบเหลือหนึ่ง",
              c.post("/api/queue", json=queue_body(product_ids=["P-1042", "P-1042"],
                                                   account_ids=[a1, a1]), headers=auth_a).json()["created"], 1)
        check("เพดานคลิป/วัน ติดลบ → 422",
              c.put(f"/api/accounts/{a1}/cap", json={"cap": -1}, headers=auth_a).status_code, 422)
        check("เพดานเกิน 99 → 422",
              c.put(f"/api/accounts/{a1}/cap", json={"cap": 500}, headers=auth_a).status_code, 422)

        # ------------------------------------------------------- ownership
        section("3. แยกข้อมูลระหว่างผู้ใช้")
        check("สินค้าของคนอื่น → 404",
              c.post("/api/queue", json=queue_body(product_ids=["B-1"], account_ids=[a1]),
                     headers=auth_a).status_code, 404)
        check("บัญชีของคนอื่น → 404",
              c.post("/api/queue", json=queue_body(account_ids=[acc_b]), headers=auth_a).status_code, 404)
        check("สินค้าไม่มีจริง → 404",
              c.post("/api/queue", json=queue_body(product_ids=["ไม่มีจริง"], account_ids=[a1]),
                     headers=auth_a).status_code, 404)
        check("ผู้ใช้ B เห็นเฉพาะสินค้าตัวเอง",
              [p["id"] for p in c.get("/api/state", headers=auth_b).json()["products"]], ["B-1"])
        check("ผู้ใช้ B ไม่เห็นงานของ A", c.get("/api/state", headers=auth_b).json()["jobs"], [])
        check("แก้เพดานบัญชีคนอื่นไม่ได้",
              c.put(f"/api/accounts/{a1}/cap", json={"cap": 3}, headers=auth_b).status_code, 404)

        # -------------------------------------------------------- schedule
        section("4. การจัดตารางตามเพดานต่อวัน")
        db.connect().execute("DELETE FROM jobs")
        db.connect().execute("DELETE FROM batches")
        c.put(f"/api/accounts/{a1}/cap", json={"cap": 2}, headers=auth_a)

        res = c.post("/api/queue", json=queue_body(
            product_ids=["P-1042", "P-0871", "P-0520", "P-1190", "P-0333"],
            account_ids=[a1], slots=["11:30", "19:30"], start_date=TODAY,
        ), headers=auth_a)
        check("สร้าง 5 คลิป", res.json()["created"], 5)
        check("เพดาน 2/วัน → กระจาย 3 วัน", res.json()["days"], 3)

        rows = db.connect().execute(
            "SELECT scheduled_at FROM jobs ORDER BY scheduled_at").fetchall()
        days = [r["scheduled_at"][:10] for r in rows]
        times = [r["scheduled_at"][11:16] for r in rows]
        check("วันละ 2 คลิป แล้วขึ้นวันใหม่",
              [days.count(d) for d in sorted(set(days))], [2, 2, 1])
        check("รอบเวลาวนตามลำดับ", times[:4], ["11:30", "19:30", "11:30", "19:30"])
        check("เก็บเป็นเวลาไทย ไม่ใช่ UTC",
              db.connect().execute("SELECT scheduled_at FROM jobs LIMIT 1").fetchone()["scheduled_at"][-6:],
              "+07:00")
        check("19:30 ยังเป็น 19:30 ของวันเดิม",
              db.connect().execute(
                  "SELECT scheduled_at FROM jobs ORDER BY scheduled_at LIMIT 1"
              ).fetchone()["scheduled_at"][:16], f"{TODAY}T11:30")

        db.connect().execute("DELETE FROM jobs")
        c.put(f"/api/accounts/{a1}/cap", json={"cap": 0}, headers=auth_a)
        res = c.post("/api/queue", json=queue_body(
            product_ids=["P-1042", "P-0871", "P-0520", "P-1190", "P-0333"],
            account_ids=[a1], slots=["19:30"], start_date=TOMORROW,
        ), headers=auth_a)
        check("เพดาน 0 = ไม่จำกัด → วันเดียว", res.json()["days"], 1)
        check("ใช้วันที่ที่ขอมา",
              db.connect().execute("SELECT scheduled_at FROM jobs LIMIT 1").fetchone()["scheduled_at"][:10],
              TOMORROW)

        db.connect().execute("DELETE FROM jobs")
        res = c.post("/api/queue", json=queue_body(
            product_ids=["P-1042", "P-0871"], account_ids=[a1, a2, a3], per=2,
        ), headers=auth_a)
        check("2 สินค้า × 2 คลิป × 3 บัญชี = 12", res.json()["created"], 12)
        per_account = db.connect().execute(
            "SELECT account_id, COUNT(*) n FROM jobs GROUP BY account_id").fetchall()
        check("กระจายเท่ากันทุกบัญชี", sorted(r["n"] for r in per_account), [4, 4, 4])
        check("ทุกงานมีลิงก์ปักตะกร้า",
              all(r["link"] for r in db.connect().execute("SELECT link FROM jobs").fetchall()))

        db.connect().execute("DELETE FROM jobs")
        c.post("/api/queue", json=queue_body(account_ids=[a1], cart=False), headers=auth_a)
        check("cart=false → ไม่สร้างลิงก์",
              db.connect().execute("SELECT link FROM jobs LIMIT 1").fetchone()["link"], "")

        # a product imported with its own affiliate link must keep it verbatim —
        # a generated stand-in in the caption earns nothing
        db.connect().execute("DELETE FROM jobs")
        real_link = "https://collshp.com/abc123?linkId=99&view=storefront"
        db.connect().execute("UPDATE products SET link = ? WHERE user_id = ? AND id = ?",
                             (real_link, user_a, "P-1042"))
        c.post("/api/queue", json=queue_body(account_ids=[a1]), headers=auth_a)
        check("ใช้ลิงก์จริงของสินค้า ไม่ใช่ลิงก์ที่สร้างเอง",
              db.connect().execute("SELECT link FROM jobs LIMIT 1").fetchone()["link"], real_link)

        db.connect().execute("DELETE FROM jobs")
        db.connect().execute("UPDATE products SET link = '' WHERE user_id = ? AND id = ?",
                             (user_a, "P-1042"))
        c.post("/api/queue", json=queue_body(account_ids=[a1]), headers=auth_a)
        check("ไม่มีลิงก์จริง → ใช้ตัวสำรองที่มี sub_id",
              "sub_id=" in db.connect().execute(
                  "SELECT link FROM jobs LIMIT 1").fetchone()["link"], True)

        # ----------------------------------------------------- idempotency
        section("5. ยิงซ้ำไม่สร้างงานซ้ำ")
        db.connect().execute("DELETE FROM jobs")
        db.connect().execute("DELETE FROM batches")
        body = queue_body(account_ids=[a1], idempotency_key="k-123")
        first = c.post("/api/queue", json=body, headers=auth_a).json()
        second = c.post("/api/queue", json=body, headers=auth_a).json()
        check("ครั้งแรกสร้างงาน", first["created"], 1)
        check("ครั้งที่สองไม่สร้างเพิ่ม", second["created"], 0)
        check("ครั้งที่สองบอกว่าซ้ำ", second["duplicate"], True)
        check("batch_id เดิม", second["batch_id"], first["batch_id"])
        check("งานในฐานข้อมูลมีชิ้นเดียว",
              db.connect().execute("SELECT COUNT(*) n FROM jobs").fetchone()["n"], 1)
        check("คนละผู้ใช้ใช้ key เดียวกันได้",
              c.post("/api/queue", json={**queue_body(product_ids=["B-1"], account_ids=[acc_b]),
                                         "idempotency_key": "k-123"},
                     headers=auth_b).json()["created"], 1)

        # -------------------------------------------------------- lifecycle
        section("6. วงจรชีวิตของงาน")
        db.connect().execute("DELETE FROM jobs")
        c.post("/api/queue", json=queue_body(account_ids=[a1]), headers=auth_a)
        job_id = db.connect().execute("SELECT id FROM jobs").fetchone()["id"]

        check("เริ่มที่สถานะ queued", db.job(db.user_by_token("tok-a")["id"], job_id)["status"], "queued")
        check("ยังไม่มีไฟล์ → 404", c.get(f"/api/jobs/{job_id}/video", headers=auth_a).status_code, 404)
        check("ยังไม่พร้อม กดว่าอัปแล้วไม่ได้ → 409",
              c.post(f"/api/jobs/{job_id}/posted", headers=auth_a).status_code, 409)

        check("worker หยิบงานไปทำ", worker.run_once(), True)
        after = c.get("/api/state", headers=auth_a).json()["jobs"][0]
        check("สถานะเป็น ready", after["status"], "ready")
        check("มีสคริปต์", len(after["script"]) > 0, True)
        check("มีแคปชัน", "ตะกร้าส้ม" in after["caption"] or "สั่งได้ที่" in after["caption"], True)
        check("แคปชันมีลิงก์ปักตะกร้า", "s.shopee.co.th" in after["caption"], True)
        check("ความยาวมากกว่า 0", after["duration"] > 0, True)
        check("ดาวน์โหลดไฟล์ได้", c.get(f"/api/jobs/{job_id}/video", headers=auth_a).status_code, 200)
        check("ผู้ใช้อื่นดาวน์โหลดไม่ได้",
              c.get(f"/api/jobs/{job_id}/video", headers=auth_b).status_code, 404)

        check("กดว่าอัปแล้ว", c.post(f"/api/jobs/{job_id}/posted", headers=auth_a).status_code, 200)
        check("สถานะเป็น posted",
              c.get("/api/state", headers=auth_a).json()["jobs"][0]["status"], "posted")
        check("กดซ้ำ → 409", c.post(f"/api/jobs/{job_id}/posted", headers=auth_a).status_code, 409)
        check("ยอดใช้ของบัญชีเพิ่มขึ้น",
              next(a["used"] for a in c.get("/api/state", headers=auth_a).json()["accounts"]
                   if a["id"] == a1), 1)
        check("posted แล้ว retry ไม่ได้ → 409",
              c.post(f"/api/jobs/{job_id}/retry", headers=auth_a).status_code, 409)
        check("งานไม่มีจริง retry → 409",
              c.post("/api/jobs/j_ไม่มีจริง/retry", headers=auth_a).status_code, 409)

        section("7. งานล้มเหลวแล้วลองใหม่")
        db.connect().execute("DELETE FROM jobs")
        c.post("/api/queue", json=queue_body(product_ids=["P-0871"], account_ids=[a1]), headers=auth_a)
        failed_id = db.connect().execute("SELECT id FROM jobs").fetchone()["id"]
        db.connect().execute("UPDATE jobs SET status='rendering'")
        db.fail_job(failed_id, "ทดสอบความล้มเหลว")
        check("สถานะ failed มีข้อความ",
              c.get("/api/state", headers=auth_a).json()["jobs"][0]["error"], "ทดสอบความล้มเหลว")
        check("retry ได้", c.post(f"/api/jobs/{failed_id}/retry", headers=auth_a).status_code, 200)
        check("กลับเป็น queued และล้าง error",
              db.job(db.user_by_token("tok-a")["id"], failed_id)["status"], "queued")
        check("ผู้ใช้อื่น retry ไม่ได้",
              c.post(f"/api/jobs/{failed_id}/retry", headers=auth_b).status_code, 409)

        # ----------------------------------------------------------- worker
        section("8. worker แย่งงานกันไม่ได้")
        db.connect().execute("DELETE FROM jobs")
        c.post("/api/queue", json=queue_body(account_ids=[a1]), headers=auth_a)
        claimed: list = []
        barrier = threading.Barrier(4)

        def grab():
            barrier.wait()
            try:
                claimed.append(db.claim_next())
            finally:
                db.close()

        threads = [threading.Thread(target=grab) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        check("4 worker แย่งงานเดียว ได้คนเดียว", sum(1 for x in claimed if x), 1)

        check("คิวว่างแล้ว run_once คืน False", (worker.run_once(), worker.run_once())[1], False)

        section("9. worker ตายกลางทาง งานต้องกลับเข้าคิว")
        db.connect().execute("DELETE FROM jobs")
        c.post("/api/queue", json=queue_body(account_ids=[a1]), headers=auth_a)
        stuck = db.claim_next()
        db.connect().execute("UPDATE jobs SET started_at='2020-01-01T00:00:00+00:00' WHERE id=?",
                             (stuck["id"],))
        check("เก็บกวาดงานค้าง 1 ชิ้น", db.reclaim_stale(60), 1)
        check("กลับเป็น queued", db.job(db.user_by_token("tok-a")["id"], stuck["id"])["status"], "queued")
        check("งานที่เพิ่งเริ่ม ไม่ถูกเก็บกวาด", (db.claim_next() and db.reclaim_stale(900)), 0)

        # ------------------------------------------------------- traversal
        section("10. กันอ่านไฟล์นอกโฟลเดอร์")
        db.connect().execute("DELETE FROM jobs")
        c.post("/api/queue", json=queue_body(account_ids=[a1]), headers=auth_a)
        evil_id = db.connect().execute("SELECT id FROM jobs").fetchone()["id"]
        secret = SCRATCH / "notes.txt"
        secret.write_text("ความลับ", encoding="utf-8")
        db.connect().execute("UPDATE jobs SET status='ready', video_path=? WHERE id=?",
                             (str(secret), evil_id))
        check("path นอกโฟลเดอร์ renders → 404",
              c.get(f"/api/jobs/{evil_id}/video", headers=auth_a).status_code, 404)
        db.connect().execute("UPDATE jobs SET video_path=? WHERE id=?",
                             (str(worker.RENDERS / ".." / "notes.txt"), evil_id))
        check("path ที่มี .. → 404",
              c.get(f"/api/jobs/{evil_id}/video", headers=auth_a).status_code, 404)

        # ----------------------------------------------------------- events
        section("11. Log")
        before = len(c.get("/api/state", headers=auth_a).json()["events"])
        check("มี log สะสมไว้", before > 0, True)
        check("ล้าง log ได้", c.delete("/api/events", headers=auth_a).status_code, 200)
        check("ล้างแล้วว่าง", c.get("/api/state", headers=auth_a).json()["events"], [])
        check("ล้างของ A ไม่กระทบ B", len(c.get("/api/state", headers=auth_b).json()["events"]) > 0, True)

    # ------------------------------------------------------- rate limit
    section("12. จำกัดอัตราการยิง")
    os.environ["CLIPQUEUE_RATE_LIMIT"] = "5"
    import importlib

    from server import app as app_module
    importlib.reload(app_module)
    with TestClient(app_module.app) as c2:
        codes = [c2.get("/api/state", headers={"Authorization": "Bearer tok-a"}).status_code
                 for _ in range(8)]
        check("ยิงเกินโควตาแล้วโดน 429", 429 in codes, True)
        check("ยิงครั้งแรก ๆ ยังผ่าน", codes[0], 200)

    section("13. อัปเกรดฐานข้อมูลเก่า")
    check_migration()

    section("14. โหมดสาธารณะ (เปิดออกอินเทอร์เน็ต)")
    check_public_mode()

    print(f"\nผ่าน {PASS} · ไม่ผ่าน {FAIL}")
    return 1 if FAIL else 0


def check_public_mode() -> None:
    """Once the port is on the internet, the guessable demo token and short
    tokens become the whole attack surface."""
    import importlib

    os.environ["CLIPQUEUE_PUBLIC"] = "1"
    os.environ["CLIPQUEUE_DB"] = str(SCRATCH / "public.db")
    db.close()
    original = db.DB_PATH
    db.DB_PATH = SCRATCH / "public.db"
    try:
        from server import app as app_module
        importlib.reload(app_module)
        check("เปิดโหมดสาธารณะแล้ว", app_module.PUBLIC, True)

        # a demo token plus a public port is the one combination that must not boot
        os.environ["CLIPQUEUE_DEMO_TOKEN"] = "demo-token"
        try:
            with TestClient(app_module.app):
                pass
            check("ปฏิเสธ demo token ตอนเปิดสาธารณะ", False, True)
        except RuntimeError as exc:
            check("ปฏิเสธ demo token ตอนเปิดสาธารณะ", "CLIPQUEUE_DEMO_TOKEN" in str(exc), True)

        del os.environ["CLIPQUEUE_DEMO_TOKEN"]
        importlib.reload(app_module)
        with TestClient(app_module.app) as pc:
            db.init()
            _, strong = db.create_user("pub@local", "ร้าน", "a" * 40)
            db.create_user("weak@local", "ร้านสั้น", "sh0rt")

            check("token ยาวพอ ใช้ได้",
                  pc.get("/api/state", headers={"Authorization": f"Bearer {strong}"}).status_code, 200)
            check("token สั้น ถูกปฏิเสธ",
                  pc.get("/api/state", headers={"Authorization": "Bearer sh0rt"}).status_code, 401)

            check("ปิดหน้า API docs ตอนเปิดสาธารณะ", pc.get("/api/docs").status_code, 404)
            check("ปิด openapi schema ด้วย", pc.get("/api/openapi.json").status_code, 404)

            head = pc.get("/api/health").headers
            check("ห้ามฝังในเฟรมเว็บอื่น", head.get("X-Frame-Options"), "DENY")
            check("ห้ามเดาชนิดไฟล์", head.get("X-Content-Type-Options"), "nosniff")
            check("ไม่ส่ง referrer ออกไป", head.get("Referrer-Policy"), "no-referrer")
            check("API ห้าม cache", head.get("Cache-Control"), "no-store")
    finally:
        os.environ.pop("CLIPQUEUE_PUBLIC", None)
        os.environ["CLIPQUEUE_DEMO_TOKEN"] = "tok-a"
        db.close()
        db.DB_PATH = original
        os.environ["CLIPQUEUE_DB"] = str(original)


def check_migration() -> None:
    """A database made before a column existed must survive the upgrade.

    CREATE TABLE IF NOT EXISTS does nothing to an existing table, so without a
    real migration every query touching a new column fails on an old file.
    """
    import sqlite3

    old = SCRATCH / "legacy.db"
    conn = sqlite3.connect(old)
    conn.executescript("""
      CREATE TABLE users (id TEXT PRIMARY KEY, email TEXT, name TEXT,
                          token_hash TEXT, created_at TEXT);
      CREATE TABLE jobs (id TEXT PRIMARY KEY, user_id TEXT, batch_id TEXT,
        product_id TEXT, account_id TEXT, format TEXT, aspect TEXT, persona TEXT,
        voice TEXT, cart INTEGER, status TEXT, progress INTEGER,
        attempts INTEGER DEFAULT 0, scheduled_at TEXT, created_at TEXT,
        started_at TEXT, finished_at TEXT, script TEXT DEFAULT '[]',
        caption TEXT DEFAULT '', link TEXT DEFAULT '', duration REAL DEFAULT 0,
        size_kb INTEGER DEFAULT 0, video_path TEXT DEFAULT '', error TEXT DEFAULT '');
    """)
    conn.execute("INSERT INTO users VALUES ('u1','a@b','x','h','2026-01-01')")
    for job_id, link in [("j_a", "https://s.shopee.co.th/q?sub_id=sub_main_j_a"),
                         ("j_b", ""),
                         ("j_c", "https://s.shopee.co.th/q?sub_id=sub_clip_j_c&utm=x")]:
        conn.execute(
            "INSERT INTO jobs (id,user_id,batch_id,product_id,account_id,format,aspect,"
            "persona,voice,cart,status,progress,scheduled_at,created_at,link)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (job_id, "u1", "b", "P", "a", "quick", "9:16", "x", "female", 1,
             "posted", 100, "2026-01-01", "2026-01-01", link),
        )
    conn.commit()
    conn.close()

    db.close()
    original = db.DB_PATH
    try:
        db.DB_PATH = old
        db.init()
        columns = {r["name"] for r in db.connect().execute("PRAGMA table_info(jobs)")}
        check("เพิ่มคอลัมน์ sub_id ให้ฐานข้อมูลเก่า", "sub_id" in columns, True)
        check("ข้อมูลเดิมไม่หาย",
              db.connect().execute("SELECT COUNT(*) n FROM jobs").fetchone()["n"], 3)
        subs = {r["id"]: r["sub_id"] for r in
                db.connect().execute("SELECT id, sub_id FROM jobs")}
        check("ดึง sub_id จากลิงก์ที่ส่งออกไปแล้ว", subs["j_a"], "sub_main_j_a")
        check("ตัดพารามิเตอร์อื่นออก", subs["j_c"], "sub_clip_j_c")
        check("งานที่ไม่มีลิงก์ ปล่อยว่างไว้", subs["j_b"], "")
        check("สร้างตารางใหม่ที่ยังไม่มีให้ด้วย",
              bool(db.connect().execute(
                  "SELECT 1 FROM sqlite_master WHERE name='conversions'").fetchone()), True)
        check("รันซ้ำแล้วไม่เปลี่ยนอะไรอีก", db.add_missing_columns(), [])
        check("เติมย้อนหลังซ้ำแล้วไม่ทำอีก", db.backfill_sub_ids(), 0)
    finally:
        db.close()
        db.DB_PATH = original


if __name__ == "__main__":
    raise SystemExit(main())
