"""ทดสอบการหาโฟลเดอร์ตัวละครและการเรียงช็อตคนสลับสินค้า

ไม่ต้องใช้ ffmpeg — ทดสอบเฉพาะตรรกะการเลือกและเรียงไฟล์

    python tools/test_persona.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import persona  # noqa: E402

PASS = FAIL = 0


def check(label, got, want=True):
    global PASS, FAIL
    if got == want:
        PASS += 1
        print(f"  ok    {label}")
    else:
        FAIL += 1
        print(f"  FAIL  {label}\n          ได้ {got!r}\n          คาด {want!r}")


def P(*names):
    return [Path(n) for n in names]


def kinds(plan, people):
    """แปลงแผนเป็นสตริงอ่านง่าย: ค = คน, ส = สินค้า"""
    return "".join("ค" if p in people else "ส" for p in plan)


def main() -> int:
    print("\n1. แปลงชื่อเป็น slug")
    check("ตัดอักขระพิเศษ", persona.slug("น้องมายด์ สายบิวตี้"), "น้องมายด์-สายบิวตี้")
    check("ตัวพิมพ์เล็ก", persona.slug("Coach Bank"), "coach-bank")
    check("ตัดขีดหัวท้าย", persona.slug("  --พี่หมี--  "), "พี่หมี")

    print("\n2. หาโฟลเดอร์ตัวละคร")
    with tempfile.TemporaryDirectory() as tmp:
        persona.PERSONA_DIR = Path(tmp)
        (Path(tmp) / "น้องมายด์ สายบิวตี้").mkdir()
        (Path(tmp) / "coach-bank").mkdir()

        check("ชื่อตรงเป๊ะ", persona.folder_for("น้องมายด์ สายบิวตี้").name,
              "น้องมายด์ สายบิวตี้")
        check("ชื่อที่ถูกแปลงแล้วก็เจอ", persona.folder_for("Coach Bank").name, "coach-bank")
        check("ไม่มีตัวนี้", persona.folder_for("ไม่มีใคร"), None)
        for empty in ("", "ไม่ใช้ตัวละคร", "none", "-"):
            check(f"'{empty}' = ไม่ใส่คน", persona.folder_for(empty), None)

        print("\n3. หยิบไฟล์สื่อ")
        folder = Path(tmp) / "น้องมายด์ สายบิวตี้"
        for n in ["02.jpg", "01.jpg", "03.mp4", "readme.txt", "04.PNG"]:
            (folder / n).write_bytes(b"x")
        got = [p.name for p in persona.shots("น้องมายด์ สายบิวตี้")]
        # วิดีโอมาก่อน เพราะช่องสำหรับคนมีจำกัด ช็อตที่ขยับได้คุ้มกว่าภาพนิ่ง
        check("วิดีโอขึ้นก่อน แล้วรูปเรียงตามชื่อ ตัดไฟล์ที่ไม่ใช่สื่อออก", got,
              ["03.mp4", "01.jpg", "02.jpg", "04.PNG"])
        check("รู้จักวิดีโอ", persona.is_video(Path("a/03.mp4")), True)
        check("รูปไม่ใช่วิดีโอ", persona.is_video(Path("a/01.jpg")), False)
        check("ไม่มีโฟลเดอร์ = ไม่มีช็อต", persona.shots("ไม่มีใคร"), [])

    print("\n4. เรียงช็อต — คนเปิดและปิด สินค้าอยู่กลาง")
    products = P("p1", "p2", "p3", "p4", "p5")
    people = P("c1", "c2")
    plan = persona.interleave(products, people)
    check("ช็อตแรกเป็นคน", plan[0] in people, True)
    check("ช็อตสุดท้ายเป็นคน", plan[-1] in people, True)
    check("จำนวนช็อตเท่าเดิม", len(plan), len(products))
    check("รูปแบบ", kinds(plan, people), "คสสสค")
    check("สัดส่วนคนตั้งต้น", persona.PERSON_SHARE, 0.55)

    print("\n5. มีคนรูปเดียว")
    one = P("c1")
    plan = persona.interleave(products, one)
    check("ใช้เปิดอย่างเดียว ไม่ซ้ำท้าย", kinds(plan, one), "คสสสส")

    print("\n6. มีไฟล์คนเยอะ — ใช้ตาม PERSON_SHARE แต่ต้องเหลือที่ให้สินค้า")
    many = P("c1", "c2", "c3", "c4", "c5", "c6")
    plan = persona.interleave(products, many)
    person_count = sum(1 for p in plan if p in many)
    want = min(len(many), len(products) - 1,
               max(1, round(len(products) * persona.PERSON_SHARE)))
    check("จำนวนคนตาม PERSON_SHARE", person_count, want)
    check("ยังเหลือช็อตสินค้าอย่างน้อยหนึ่งช็อต", person_count < len(plan), True)
    check("ยังเปิดและปิดด้วยคน", plan[0] in many and plan[-1] in many, True)

    print("\n7. ช็อตเยอะขึ้น คนแทรกกลางด้วย")
    long_products = P(*[f"p{i}" for i in range(9)])
    plan = persona.interleave(long_products, many)
    shape = kinds(plan, many)
    middle = shape[1:-1]
    check("มีคนแทรกตรงกลาง", "ค" in middle, True)
    check("ยาวเท่าเดิม", len(plan), 9)
    check("เปิดปิดด้วยคน", shape[0] == "ค" and shape[-1] == "ค", True)

    print("\n8. กรณีขอบ")
    check("ไม่มีคนเลย → สินค้าล้วน",
          persona.interleave(products, []), products)
    check("ไม่มีสินค้า → คนล้วน",
          persona.interleave([], people), people)
    check("ว่างทั้งคู่", persona.interleave([], []), [])
    check("สินค้าชิ้นเดียว + คนหนึ่ง ยังได้อย่างน้อย 2 ช็อต",
          len(persona.interleave(P("p1"), P("c1"))) >= 2, True)
    check("ไฟล์คนน้อยกว่าช่องที่ต้องการ → วนซ้ำได้ ไม่พัง",
          len(persona.interleave(products, P("c1"), count=5)), 5)

    print(f"\nผ่าน {PASS} · ไม่ผ่าน {FAIL}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
