"""Exercise src/bullets.py against a stub client — no API key, no spend.

Covers the parts that would otherwise only be proven by a live call: batching,
the on-disk cache, length filtering, refusal handling, and a response that
comes back without the parsed object.

    python tools/test_bullets.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import bullets  # noqa: E402

PRODUCTS = [
    {"id": f"P-{i:04d}", "name": f"สินค้าทดสอบชิ้นที่ {i}", "price": 100 + i, "sold": 10 * i}
    for i in range(1, 15)
]


class StubBlock:
    type = "text"

    def __init__(self, parsed):
        self.parsed_output = parsed


class StubMessage:
    def __init__(self, parsed, stop_reason="end_turn", category=None):
        self.content = [StubBlock(parsed)] if parsed is not None else []
        self.stop_reason = stop_reason
        self.stop_details = type("D", (), {"category": category})() if category else None


class StubMessages:
    """Answers like the real API would: one entry per item_id it was given."""

    def __init__(self, outer):
        self.outer = outer

    def parse(self, *, model, max_tokens, system, output_config, output_format, messages):
        self.outer.calls += 1
        self.outer.models.append(model)
        prompt = messages[0]["content"]
        ids = [line.split("item_id: ")[1].split(" |")[0]
               for line in prompt.splitlines() if "item_id: " in line]
        self.outer.batch_sizes.append(len(ids))

        if self.outer.mode == "refusal":
            return StubMessage(None, stop_reason="refusal", category="general_harms")
        if self.outer.mode == "unparsed":
            return StubMessage(None)

        return StubMessage(output_format(products=[
            {"item_id": pid, "bullets": [
                "  ใช้งานง่าย ไม่ต้องตั้งค่าอะไร  ",      # trimmed, in range
                "สั้นไป",                                  # dropped: too short
                "ก" * 60,                                   # dropped: too long
                "พกพาสะดวก ใส่กระเป๋าได้สบาย",             # in range
            ]} for pid in ids
        ]))


class StubClient:
    def __init__(self, mode="ok"):
        self.mode = mode
        self.calls = 0
        self.batch_sizes = []
        self.models = []
        self.messages = StubMessages(self)


def check(label, got, want):
    ok = got == want
    print(f"  {'ok  ' if ok else 'FAIL'} {label}: {got!r}" + ("" if ok else f" (คาดว่า {want!r})"))
    return ok


def main() -> int:
    passed = True
    with tempfile.TemporaryDirectory() as tmp:
        bullets.CACHE = Path(tmp) / "cache.json"

        print("1. เรียกครั้งแรก — แบ่ง batch และกรองความยาว")
        client = StubClient()
        out = bullets.generate(PRODUCTS, client=client)
        passed &= check("จำนวนสินค้าที่ได้", len(out), len(PRODUCTS))
        passed &= check("จำนวนครั้งที่เรียก API", client.calls, 3)  # 14 / BATCH 6
        passed &= check("ขนาดแต่ละ batch", client.batch_sizes, [6, 6, 2])
        passed &= check("โมเดลที่ใช้", client.models[0], "claude-opus-5-5")
        passed &= check("บรรทัดที่ผ่านตัวกรอง", out["P-0001"],
                        ["ใช้งานง่าย ไม่ต้องตั้งค่าอะไร", "พกพาสะดวก ใส่กระเป๋าได้สบาย"])

        print("\n2. เรียกซ้ำ — ต้องอ่านจาก cache ไม่เรียก API เลย")
        again = StubClient()
        out2 = bullets.generate(PRODUCTS, client=again)
        passed &= check("จำนวนครั้งที่เรียก API", again.calls, 0)
        passed &= check("ผลลัพธ์เหมือนเดิม", out2 == out, True)

        print("\n3. สินค้าเปลี่ยนชื่อ — ต้องเขียนใหม่เฉพาะชิ้นนั้น")
        renamed = [dict(p) for p in PRODUCTS]
        renamed[0]["name"] = "สินค้าทดสอบชิ้นที่ 1 รุ่นใหม่"
        third = StubClient()
        bullets.generate(renamed, client=third)
        passed &= check("จำนวนครั้งที่เรียก API", third.calls, 1)
        passed &= check("ขนาด batch", third.batch_sizes, [1])

        print("\n4. โมเดลปฏิเสธ — ต้องโยน error ที่อ่านรู้เรื่อง")
        try:
            bullets.generate([{"id": "X", "name": "ของต้องห้าม", "price": 1, "sold": 0}],
                             client=StubClient("refusal"))
            passed &= check("โยน error", False, True)
        except RuntimeError as exc:
            passed &= check("ข้อความบอกสาเหตุ", "ปฏิเสธ" in str(exc) and "general_harms" in str(exc), True)

        print("\n5. ไม่มีผลลัพธ์ตาม schema — ต้องไม่เงียบ")
        try:
            bullets.generate([{"id": "Y", "name": "ของทดสอบ", "price": 1, "sold": 0}],
                             client=StubClient("unparsed"))
            passed &= check("โยน error", False, True)
        except RuntimeError as exc:
            passed &= check("ข้อความบอกสาเหตุ", "schema" in str(exc), True)

    print("\n" + ("ผ่านทั้งหมด" if passed else "มีเคสที่ไม่ผ่าน"))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
