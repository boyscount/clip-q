"""Tests for the conversion-report sync, against a stub Shopee client.

The parts worth proving without a live key: money stays exact, a sub-id maps
back to the right clip, an order that changes status overwrites instead of
duplicating, cancelled orders never inflate earnings, and report rows that are
not ours are kept rather than dropped.

    python tools/test_stats.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCRATCH = Path(tempfile.mkdtemp(prefix="clipqueue-stats-"))
os.environ["CLIPQUEUE_DB"] = str(SCRATCH / "test.db")
os.environ["CLIPQUEUE_RENDERS"] = str(SCRATCH / "renders")
os.environ["CLIPQUEUE_WORKER"] = "0"
os.environ["CLIPQUEUE_FAKE_RENDER"] = "1"
os.environ["CLIPQUEUE_DEMO_TOKEN"] = "tok-a"

from fastapi.testclient import TestClient  # noqa: E402

from server import db, stats  # noqa: E402
from server.app import app  # noqa: E402
from server.service import clip_sub_id  # noqa: E402
from src.shopee import to_satang  # noqa: E402

PASS = FAIL = 0


def check(label, got, want=True):
    global PASS, FAIL
    if got == want:
        PASS += 1
        print(f"  ok    {label}")
    else:
        FAIL += 1
        print(f"  FAIL  {label}\n          ได้ {got!r}\n          คาด {want!r}")


class StubShopee:
    """Returns whatever rows the test hands it, in the client's output shape."""

    def __init__(self, rows):
        self.rows = rows
        self.windows = []

    def conversions(self, start, end, limit=100, max_rows=5000):
        self.windows.append((start, end))
        return self.rows


def row(conv_id, sub_id, *, commission="12.34", amount="399.00",
        status="completed", qty=1, name="หูฟัง"):
    return {
        "conversion_id": conv_id, "sub_id": sub_id, "purchase_time": 1790000000,
        "status": status, "item_id": "P-1042", "item_name": name, "qty": qty,
        "amount_satang": to_satang(amount), "commission_satang": to_satang(commission),
    }


def main() -> int:
    print("\n1. แปลงจำนวนเงินเป็นสตางค์")
    for text, want in [("399.00", 39900), ("12.34", 1234), ("0.1", 10), ("1234.56", 123456),
                       ("0", 0), ("", 0), (None, 0), ("ไม่ใช่ตัวเลข", 0), (59.9, 5990)]:
        check(f"{text!r} → {want}", to_satang(text), want)
    check("0.1 + 0.2 ไม่เพี้ยนแบบ float", to_satang("0.1") + to_satang("0.2"), 30)

    print("\n2. sub-id ผูกกับคลิป ไม่ใช่แค่บัญชี")
    long_sub = clip_sub_id("sub_" + "x" * 60, "j_abc123")
    check("ตัดไม่ให้เกิน 50 ตัวอักษร", len(long_sub) <= 50, True)
    check("มีรหัสงานอยู่ในนั้น", "j_" in clip_sub_id("sub_main", "j_abc123"), True)
    check("คนละงานได้คนละ sub-id",
          clip_sub_id("sub_main", "j_a") != clip_sub_id("sub_main", "j_b"), True)

    with TestClient(app) as c:
        auth = {"Authorization": "Bearer tok-a"}
        user_id = db.user_by_token("tok-a")["id"]
        import json
        db.replace_products(user_id, json.loads(
            (ROOT / "server" / "seed_products.json").read_text(encoding="utf-8")))
        account = db.accounts(user_id)[0]

        made = c.post("/api/queue", json={
            "product_ids": ["P-1042", "P-0871"], "account_ids": [account["id"]],
            "slots": ["19:30"], "format": "quick", "aspect": "9:16",
            "persona": "พี่หมี", "voice": "female", "per": 1, "cart": True,
            "start_date": date.today().isoformat(),
        }, headers=auth)
        check("สร้างงานได้ 2 ชิ้น", made.json()["created"], 2)

        jobs = db.jobs(user_id)
        job_a, job_b = jobs[0], jobs[1]
        check("ทุกงานมี sub-id", all(j["sub_id"] for j in jobs), True)
        check("sub-id ไม่ซ้ำกัน", len({j["sub_id"] for j in jobs}), 2)
        check("ลิงก์พก sub-id ของตัวเอง", job_a["sub_id"] in job_a["link"], True)

        print("\n3. ดึงรายงานแล้วจับคู่กลับไปที่คลิป")
        stub = StubShopee([
            row("o1:i1", job_a["sub_id"], commission="12.34"),
            row("o2:i1", job_b["sub_id"], commission="20.00"),
            row("o3:i1", "sub_ของคนอื่น", commission="99.00"),
        ])
        result = stats.sync(user_id, days=7, client=stub)
        check("อ่านมา 3 แถว", result["rows"], 3)
        check("ใหม่ทั้ง 3", result["new"], 3)
        check("จับคู่คลิปได้ 2", result["matched"], 2)
        check("ไม่ใช่ลิงก์เรา 1", result["unmatched"], 1)
        check("ช่วงเวลาย้อนหลัง 7 วัน",
              round((stub.windows[0][1] - stub.windows[0][0]) / 86400), 7)

        per_job = db.job_stats(user_id)
        check("คลิป A ได้ค่าคอมฯ 12.34", per_job[job_a["id"]]["commissionSatang"], 1234)
        check("คลิป B ได้ค่าคอมฯ 20.00", per_job[job_b["id"]]["commissionSatang"], 2000)
        totals = db.conversion_totals(user_id)
        check("ยอดรวมนับของคนอื่นด้วย (ให้ตรงกับ Shopee)",
              totals["commissionSatang"], 1234 + 2000 + 9900)
        check("บอกจำนวนที่จับคู่ไม่ได้", totals["unmatched"], 1)

        print("\n4. ยิงซ้ำ ต้องอัปเดตไม่ใช่เพิ่มแถว")
        again = stats.sync(user_id, days=7, client=StubShopee([
            row("o1:i1", job_a["sub_id"], commission="15.00"),   # ค่าคอมฯ ถูกปรับ
            row("o2:i1", job_b["sub_id"], commission="20.00"),
            row("o3:i1", "sub_ของคนอื่น", commission="99.00"),
        ]))
        check("ไม่มีแถวใหม่", again["new"], 0)
        check("อัปเดต 3 แถว", again["updated"], 3)
        check("จำนวนแถวรวมยังเท่าเดิม", db.conversion_totals(user_id)["rows"], 3)
        check("ค่าคอมฯ ถูกเขียนทับเป็นค่าใหม่",
              db.job_stats(user_id)[job_a["id"]]["commissionSatang"], 1500)

        print("\n5. ออเดอร์ที่ยกเลิก ต้องไม่ถูกนับเป็นรายได้")
        stats.sync(user_id, days=7, client=StubShopee([
            row("o1:i1", job_a["sub_id"], commission="15.00", status="cancelled"),
            row("o2:i1", job_b["sub_id"], commission="20.00"),
            row("o3:i1", "sub_ของคนอื่น", commission="99.00"),
        ]))
        after = db.job_stats(user_id)
        check("คลิป A รายได้เหลือ 0", after[job_a["id"]]["commissionSatang"], 0)
        check("คลิป A นับยอดยกเลิกไว้ 1", after[job_a["id"]]["cancelled"], 1)
        check("คลิป A ออเดอร์เหลือ 0", after[job_a["id"]]["orders"], 0)
        check("คลิป B ไม่กระทบ", after[job_b["id"]]["commissionSatang"], 2000)

        print("\n6. ผ่าน API")
        state = c.get("/api/state", headers=auth).json()
        job_json = next(j for j in state["jobs"] if j["id"] == job_b["id"])
        check("งานแต่ละชิ้นพก stats มาด้วย", job_json["stats"]["commissionSatang"], 2000)
        check("state มียอดรวม", "earnings" in state, True)

        report = c.get("/api/stats", headers=auth).json()
        check("จัดอันดับโดยคลิปที่ทำเงินสุดมาก่อน",
              report["top"][0]["job_id"], job_b["id"])
        check("นับเฉพาะคลิปที่มีออเดอร์", report["clips_with_sales"], 2)
        check("days เกินเพดาน → 422",
              c.post("/api/stats/sync?days=999", headers=auth).status_code, 422)
        check("days = 0 → 422", c.post("/api/stats/sync?days=0", headers=auth).status_code, 422)
        check("ไม่มีคีย์ Shopee → 502 พร้อมเหตุผล",
              c.post("/api/stats/sync?days=7", headers=auth).status_code, 502)
        check("token มั่วดู stats ไม่ได้",
              c.get("/api/stats", headers={"Authorization": "Bearer nope"}).status_code, 401)
        check("ไม่ส่ง token เลย",
              c.get("/api/stats").status_code, 401)

        print("\n7. ดักค่าพิกล")
        for bad in (0, -1, 91, 999):
            try:
                stats.sync(user_id, days=bad, client=StubShopee([]))
                check(f"days={bad} ต้องไม่ผ่าน", False, True)
            except ValueError:
                check(f"days={bad} ถูกปฏิเสธ", True)
        empty = stats.sync(user_id, days=1, client=StubShopee([]))
        check("รายงานว่าง ไม่ error", empty["rows"], 0)
        weird = stats.sync(user_id, days=1, client=StubShopee([
            {"conversion_id": "o9:i9", "sub_id": "", "purchase_time": None, "status": "",
             "item_id": "", "item_name": "", "qty": 0, "amount_satang": 0,
             "commission_satang": 0},
        ]))
        check("แถวที่ข้อมูลขาด ยังเก็บได้", weird["rows"], 1)
        check("sub-id ว่าง ถือว่าจับคู่ไม่ได้", weird["unmatched"], 1)

    print(f"\nผ่าน {PASS} · ไม่ผ่าน {FAIL}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
