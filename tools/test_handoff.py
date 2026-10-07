"""Tests for the semi-automatic hand-off: outbox folder, notifier, /api/next.

No network: the notifier's HTTP call is stubbed. No ffmpeg: renders are faked.

    python tools/test_handoff.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCRATCH = Path(tempfile.mkdtemp(prefix="clipqueue-handoff-"))
OUTBOX = SCRATCH / "outbox"
os.environ.update({
    "CLIPQUEUE_DB": str(SCRATCH / "test.db"),
    "CLIPQUEUE_RENDERS": str(SCRATCH / "renders"),
    "CLIPQUEUE_OUTBOX": str(OUTBOX),
    "CLIPQUEUE_OUTBOX_KEEP": "3",
    "CLIPQUEUE_FAKE_RENDER": "1",
    "CLIPQUEUE_WORKER": "0",
    "CLIPQUEUE_DEMO_TOKEN": "tok-a",
    "CLIPQUEUE_NOTIFY": "ntfy",
    "NTFY_TOPIC": "clipqueue-test",
    "CLIPQUEUE_BASE_URL": "http://192.168.1.50:8787",
})

from fastapi.testclient import TestClient  # noqa: E402

from server import db, notify, outbox, worker  # noqa: E402
from server.app import app  # noqa: E402

PASS = FAIL = 0


def check(label, got, want=True):
    global PASS, FAIL
    if got == want:
        PASS += 1
        print(f"  ok    {label}")
    else:
        FAIL += 1
        print(f"  FAIL  {label}\n          ได้ {got!r}\n          คาด {want!r}")


class FakeResponse:
    def __init__(self, code=200):
        self.status_code = code


def main() -> int:
    sent = []

    class FakeRequests:
        @staticmethod
        def post(url, **kwargs):
            sent.append({"url": url, **kwargs})
            return FakeResponse(sent and sent[-1].get("_code", 200) or 200)

    print("\n1. ชื่อไฟล์ในโฟลเดอร์ซิงก์")
    check("ตัดอักขระที่ใช้ไม่ได้ออก", outbox.slug('a/b\\c:d*e"f'), "a-b-c-d-e-f")
    check("เก็บภาษาไทยไว้", outbox.slug("หูฟังบลูทูธ"), "หูฟังบลูทูธ")
    check("ตัดความยาว", len(outbox.slug("ก" * 99)) <= 28, True)
    check("ว่างเปล่าแล้วไม่ได้ชื่อว่าง", outbox.slug("///"), "clip")
    check("ขีดล่างถูกแทน เพราะใช้เป็นตัวคั่น", outbox.slug("ของ_ดี_ราคาถูก"), "ของ-ดี-ราคาถูก")

    print("\n2. ตัวแจ้งเตือน")
    check("เปิดอยู่", notify.enabled(), True)
    check("ตั้งค่าครบ", "พร้อมใช้" in notify.describe(), True)
    notify.requests = FakeRequests  # not used; send() imports requests itself
    import server.notify as nmod
    sys.modules["requests"] = type("M", (), {"post": FakeRequests.post})
    ok = nmod.clip_ready("หูฟังบลูทูธ", "@myshop", "เหลือ 399 บาท", 3)
    check("ส่งสำเร็จ", ok, True)
    check("ยิงไปที่หัวข้อที่ตั้งไว้", sent[-1]["url"], "https://ntfy.sh/clipqueue-test")
    check("แนบลิงก์หน้า /post", "http://192.168.1.50:8787/post" in sent[-1]["data"].decode("utf-8"), True)
    check("บอกว่ารออยู่กี่คลิป", "3 คลิป" in sent[-1]["data"].decode("utf-8"), True)

    with TestClient(app) as c:
        auth = {"Authorization": "Bearer tok-a"}
        user_id = db.user_by_token("tok-a")["id"]
        db.replace_products(user_id, json.loads(
            (ROOT / "server" / "seed_products.json").read_text(encoding="utf-8")))
        account = db.accounts(user_id)[0]

        print("\n3. ยังไม่มีคลิปพร้อม")
        first = c.get("/api/next", headers=auth).json()
        check("waiting = 0", first["waiting"], 0)
        check("ไม่มีคลิป", first["clip"], None)

        c.post("/api/queue", json={
            "product_ids": ["P-1042", "P-0871", "P-0520", "P-1190", "P-0333"],
            "account_ids": [account["id"]], "slots": ["19:30"],
            "format": "quick", "aspect": "9:16", "persona": "พี่หมี",
            "voice": "female", "per": 1, "cart": True,
            "start_date": date.today().isoformat(),
        }, headers=auth)
        c.put(f"/api/accounts/{account['id']}/cap", json={"cap": 0}, headers=auth)

        print("\n4. เรนเดอร์เสร็จแล้วต้องเข้าโฟลเดอร์ + แจ้งเตือน")
        before = len(sent)
        check("worker ทำงานหนึ่งชิ้น", worker.run_once(), True)
        clips = list(OUTBOX.glob("*.mp4"))
        check("มีไฟล์คลิปในโฟลเดอร์", len(clips), 1)
        check("มีไฟล์แคปชันคู่กัน", clips[0].with_suffix(".txt").exists(), True)
        check("แคปชันมีลิงก์ปักตะกร้า",
              "s.shopee.co.th" in clips[0].with_suffix(".txt").read_text(encoding="utf-8"), True)
        job_in_name = clips[0].stem.split("_", 3)
        check("ชื่อไฟล์ลงท้ายด้วยรหัสงาน", job_in_name[3].startswith("j_"), True)
        check("ชื่อไฟล์แยกได้ 4 ส่วน", len(job_in_name), 4)
        check("ชื่อไฟล์ขึ้นต้นด้วยวันเวลา", clips[0].stem[:8].isdigit(), True)
        check("ยิงแจ้งเตือน 1 ครั้ง", len(sent) - before, 1)

        print("\n5. /api/next ให้คลิปที่ถึงคิวก่อน")
        nxt = c.get("/api/next", headers=auth).json()
        check("waiting = 1", nxt["waiting"], 1)
        check("มีแคปชัน", bool(nxt["clip"]["caption"]), True)
        check("มีลิงก์วิดีโอ", nxt["clip"]["video"].endswith("/video"), True)
        check("บอกชื่อไฟล์ให้ไปหาในมือถือ",
              nxt["clip"]["outboxName"], clips[0].name)
        check("บอกชื่อบัญชี", nxt["clip"]["account"], account["handle"])

        print("\n6. กดว่าอัปแล้ว ต้องเอาออกจากโฟลเดอร์")
        job_id = nxt["clip"]["id"]
        check("บันทึกสำเร็จ", c.post(f"/api/jobs/{job_id}/posted", headers=auth).status_code, 200)
        check("ไฟล์ถูกเอาออกจากโฟลเดอร์ซิงก์", len(list(OUTBOX.glob(f"*{job_id}*"))), 0)
        check("คิวว่างแล้ว", c.get("/api/next", headers=auth).json()["clip"], None)

        print("\n7. โฟลเดอร์ต้องไม่โตไม่หยุด (KEEP=3)")
        for _ in range(4):
            worker.run_once()
        check("เหลือแค่ 3 คลิป", len(list(OUTBOX.glob("*.mp4"))), 3)
        check("ไฟล์แคปชันถูกลบตามไปด้วย", len(list(OUTBOX.glob("*.txt"))), 3)

        print("\n8. เรียงตามเวลาที่กำหนดอัป")
        ids = [r["id"] for r in db.connect().execute(
            "SELECT id FROM jobs WHERE status='ready' ORDER BY scheduled_at, created_at").fetchall()]
        check("คลิปแรกคือตัวที่ถึงคิวก่อน", c.get("/api/next", headers=auth).json()["clip"]["id"], ids[0])

        print("\n9. หน้าเว็บมือถือ")
        page = c.get("/post")
        check("เสิร์ฟหน้า /post ได้", page.status_code, 200)
        check("เป็น HTML", "<title>อัปคลิป" in page.text, True)
        check("ไม่ฝัง token ไว้ในหน้า", "tok-a" in page.text, False)

        print("\n10. ความปลอดภัย")
        check("/api/next ต้องมี token", c.get("/api/next").status_code, 401)
        check("token ผิด", c.get("/api/next", headers={"Authorization": "Bearer x"}).status_code, 401)
        check("ปิดแจ้งเตือนแล้วไม่ยิง",
              (setattr(nmod, "PROVIDER", "none"), nmod.clip_ready("a", "b", "c", 1))[1], False)

    print(f"\nผ่าน {PASS} · ไม่ผ่าน {FAIL}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
