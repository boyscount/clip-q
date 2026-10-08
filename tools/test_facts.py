"""ทดสอบตัวเลือก "เอาข้อมูลไหนไปทำคลิป" — ข้อมูลที่พูดถึง จำนวนช็อต คนเขียนสคริปต์

ไม่เรียก ffmpeg ไม่เรียกเน็ต

    python tools/test_facts.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import script_gen, style  # noqa: E402

PASS = FAIL = 0

PRODUCT = {
    "id": "P-1", "name": "ครีมกันแดด", "price": 85, "was": 150, "sold": 2431,
    "shop": "VENITA Official", "free": True, "shots": 5,
    "bullets": ["บางเบาไม่เหนียว", "กันน้ำกันเหงื่อ", "ไม่อุดตันรูขุมขน"],
}


def check(label, got, want=True):
    global PASS, FAIL
    if got == want:
        PASS += 1
        print(f"  ok    {label}")
    else:
        FAIL += 1
        print(f"  FAIL  {label}\n          ได้ {got!r}\n          คาด {want!r}")


def said(facts, fmt="story"):
    """ทุกบรรทัดรวมกัน เพื่อถามว่าพูดถึงอะไรบ้าง — ใช้ story เพราะยาวพอจะใส่ครบ"""
    return " ".join(script_gen.plan_lines(PRODUCT, fmt, seed=3, facts=facts))


def main() -> int:
    print("\n1. ทะเบียนข้อมูล")
    check("มีครบห้าชิ้น", sorted(script_gen.ALL_FACTS),
          ["bullets", "discount", "free", "shop", "sold"])
    check("ทุกชิ้นมีชื่อไทย",
          all(v.strip() for v in script_gen.FACTS.values()), True)

    print("\n2. ไม่ส่ง facts = พูดได้หมด (ของเดิมไม่เปลี่ยน)")
    everything = said(None)
    check("พูดส่วนลด", "150" in everything, True)
    check("พูดจุดขาย", "บางเบา" in everything, True)
    check("พูดยอดขาย", "2,431" in everything, True)
    check("บอกส่งฟรี", "ส่งฟรี" in everything, True)

    print("\n3. ปิดทีละชิ้น")
    no_discount = said(["bullets", "sold", "free", "shop"])
    check("ปิดส่วนลด → ไม่พูดราคาเดิม", "150" in no_discount, False)
    check("แต่ยังพูดราคาขาย", "85" in no_discount, True)

    no_bullets = said(["discount", "sold", "free", "shop"])
    check("ปิดจุดขาย → ไม่มีข้อความจุดขาย",
          any(b in no_bullets for b in PRODUCT["bullets"]), False)

    no_sold = said(["discount", "bullets", "free", "shop"])
    check("ปิดยอดขาย → ไม่พูดจำนวนที่ขายได้", "2,431" in no_sold, False)

    no_shop = said(["discount", "bullets", "sold", "free"])
    check("ปิดชื่อร้าน → ไม่พูดชื่อร้าน", "VENITA Official" in no_shop, False)

    no_free = said(["discount", "bullets", "sold", "shop"])
    check("ปิดส่งฟรี → CTA กลับเป็นแบบธรรมดา",
          script_gen.plan_lines(PRODUCT, "quick", seed=3,
                                facts=["discount"])[-1], script_gen.CTA)
    check("ปิดส่งฟรี → ไม่โฆษณาส่งฟรี", "ส่งฟรี" in no_free, False)

    print("\n4. ปิดหมด ยังได้คลิปที่ขายของได้")
    bare = script_gen.plan_lines(PRODUCT, "quick", seed=3, facts=[])
    joined = " ".join(bare)
    check("ยังบอกชื่อสินค้า", "ครีมกันแดด" in joined, True)
    check("ยังบอกราคา", "85" in joined, True)
    check("ยังปิดด้วย CTA", bare[-1], script_gen.CTA)
    check("ไม่หลุดข้อมูลที่ปิดไว้",
          any(x in joined for x in ["150", "2,431", "VENITA Official", "บางเบา"]), False)
    check("ยาวพอใช้ ไม่ใช่สองบรรทัดโล่ง", len(bare) >= 3, True)

    print("\n5. ชื่อข้อมูลที่ไม่รู้จัก ถูกเมิน ไม่พัง")
    check("ชื่อมั่ว = เหมือนปิดหมด",
          script_gen.plan_lines(PRODUCT, "quick", seed=3, facts=["ไม่มีชิ้นนี้"]),
          script_gen.plan_lines(PRODUCT, "quick", seed=3, facts=[]))

    print("\n6. สินค้าที่ไม่มีข้อมูลชิ้นนั้น ก็แค่ข้ามไป")
    thin = {"id": "P-2", "name": "ที่คีบขนมปัง", "price": 59, "was": 0, "sold": 0,
            "shop": "", "free": False, "shots": 3, "bullets": []}
    lines = script_gen.plan_lines(thin, "quick", seed=1, facts=list(script_gen.ALL_FACTS))
    check("ยังเรนเดอร์สคริปต์ออกมาได้", len(lines) >= 2, True)
    check("ไม่มีคำว่า None หรือ 0 บาทหลุดมา",
          "None" in " ".join(lines) or "0 บาท" in " ".join(lines), False)

    print("\n7. ใช้ร่วมกับแนวคลิปได้")
    warehouse = script_gen.plan_lines(PRODUCT, "quick", seed=3,
                                      style=style.get("warehouse"), facts=["discount"])
    check("hook ยังเป็นของแนวโกดัง",
          any(warehouse[0].startswith(h.split("{")[0])
              for h in style.get("warehouse").hooks), True)
    check("และยังเคารพ facts ที่ปิดไว้",
          "บางเบา" in " ".join(warehouse), False)

    print("\n8. prompt ที่ส่งให้ Claude ตัดข้อมูลที่ปิดไว้ออกจริง")
    full = script_gen._llm_facts(PRODUCT, script_gen.ALL_FACTS)
    check("เปิดหมด → มีราคาเดิม", "150" in full, True)
    check("เปิดหมด → มียอดขาย", "2,431" in full, True)
    check("เปิดหมด → มีชื่อร้าน", "VENITA Official" in full, True)
    trimmed = script_gen._llm_facts(PRODUCT, {"bullets"})
    check("ปิดแล้วไม่ส่งราคาเดิมไปเลย", "150" in trimmed, False)
    check("ปิดแล้วไม่ส่งยอดขายไปเลย", "2,431" in trimmed, False)
    check("ราคาขายส่งไปเสมอ", "85" in trimmed, True)
    check("จุดขายที่เปิดไว้ยังส่งไป", "บางเบา" in trimmed, True)

    print(f"\nผ่าน {PASS} · ไม่ผ่าน {FAIL}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
