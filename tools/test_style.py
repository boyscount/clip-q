"""ทดสอบแนวคลิป — ทะเบียนแนว การเลือกใช้ และคำสั่ง ffmpeg ที่ประกอบออกมา

ไม่เรียก ffmpeg จริง แค่ตรวจว่าสตริงฟิลเตอร์ที่ประกอบได้ถูกต้อง

    python tools/test_style.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import persona, render, script_gen, style  # noqa: E402

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


class Recorder:
    """จำคำสั่งที่ส่งให้ ffmpeg แทนการรันจริง"""

    def __init__(self):
        self.calls = []

    def __call__(self, args, cwd=None):
        self.calls.append([str(a) for a in args])

    def joined(self):
        return "\n".join(" ".join(c) for c in self.calls)


def main() -> int:
    print("\n1. ทะเบียนแนว")
    check("มีครบสี่แนว", sorted(style.STYLES), ["clean", "conveyor", "influencer", "warehouse"])
    check("แนวตั้งต้นคือเรียบ", style.DEFAULT, "clean")
    for name, item in style.STYLES.items():
        check(f"'{name}' id ตรงกับคีย์", item.id, name)
        check(f"'{name}' มีชื่อไทย", bool(item.name.strip()), True)
        check(f"'{name}' มีคำอธิบาย", bool(item.note.strip()), True)
        check(f"'{name}' เกรดสีไม่ว่าง", bool(item.grade.strip()), True)

    print("\n2. เลือกแนว")
    check("ชื่อที่รู้จัก", style.get("warehouse").id, "warehouse")
    check("ชื่อที่ไม่รู้จัก → เรียบ", style.get("ไม่มีแนวนี้").id, "clean")
    check("None → เรียบ (งานเก่าไม่มีคอลัมน์นี้)", style.get(None).id, "clean")
    check("ค่าว่าง → เรียบ", style.get("").id, "clean")
    check("มีช่องว่างหน้าหลังก็ยังเจอ", style.get("  conveyor  ").id, "conveyor")

    print("\n3. รายการสำหรับหน้าเว็บ")
    items = style.choices()
    check("ครบทุกแนว", len(items), len(style.STYLES))
    check("มีครบสามคีย์", sorted(items[0]), ["id", "name", "note"])

    print("\n4. สายพานกับการตัดช็อต แยกกันชัด")
    check("สายพานไหลต่อเนื่อง", style.get("conveyor").belt, True)
    check("เรียบตัดเป็นช็อต", style.get("clean").belt, False)
    check("โกดังตัดแข็ง ไม่ข้ามภาพ", style.get("warehouse").xfade, 0.0)
    check("อินฟลูตัดไวกว่าเรียบ", style.get("influencer").xfade < style.get("clean").xfade, True)

    print("\n5. สัดส่วนคนต่อแนว")
    check("อินฟลูคนเยอะกว่าค่ากลาง",
          style.get("influencer").person_share > persona.PERSON_SHARE, True)
    check("โกดังคนน้อย", style.get("warehouse").person_share, 0.2)
    check("สายพานไม่ใส่คน", style.get("conveyor").person_share, 0.0)
    check("เรียบใช้ค่ากลาง", style.get("clean").person_share, None)

    products, people = P("p1", "p2", "p3", "p4", "p5"), P("c1", "c2", "c3", "c4")
    plan = persona.interleave(products, people, share=0.0)
    check("share=0 → สินค้าล้วน", plan, products)
    many = persona.interleave(products, people, share=0.7)
    check("share=0.7 ได้คนมากกว่า share=0.2",
          sum(1 for p in many if p in people)
          > sum(1 for p in persona.interleave(products, people, share=0.2) if p in people),
          True)
    check("share เกิน 0.8 ถูกตัดลง ยังเหลือสินค้า",
          sum(1 for p in persona.interleave(products, people, share=5.0) if p in people)
          < len(products), True)

    print("\n6. น้ำเสียงสคริปต์ต่างกันจริง")
    product = {"id": "P-1", "name": "ครีมกันแดด", "price": 85, "was": 150,
               "bullets": ["บางเบา", "ไม่เหนียว", "กันน้ำ"], "free": False, "shots": 5}
    influencer = script_gen.plan_lines(product, "quick", seed=1, style=style.get("influencer"))
    warehouse = script_gen.plan_lines(product, "quick", seed=1, style=style.get("warehouse"))
    plain = script_gen.plan_lines(product, "quick", seed=1)
    check("hook ของอินฟลูมาจากชุดของแนวนั้น",
          any(influencer[0].startswith(h.split("{")[0]) for h in style.get("influencer").hooks),
          True)
    check("อินฟลูกับโกดังพูดคนละแบบ", influencer[0] != warehouse[0], True)
    check("ไม่ส่งแนว ยังได้สคริปต์เหมือนเดิม", len(plain) > 0, True)
    check("ทุกแนวยังปิดด้วย CTA เดิม",
          {influencer[-1], warehouse[-1], plain[-1]}, {script_gen.CTA})

    print("\n7. เกรดสีถูกทาลงในคำสั่ง ffmpeg")
    original = render.ff.run
    try:
        for name in ("clean", "influencer", "warehouse"):
            rec = render.ff.run = Recorder()
            render.build_visual(P("a.jpg", "b.jpg"), 8.0, Path("."), style=name)
            grade = style.get(name).grade
            check(f"'{name}' ใส่เกรดสีลงทุกช็อต",
                  rec.joined().count(grade) >= 2 if grade != style.PASSTHROUGH
                  else rec.joined().count("null") >= 2, True)

        print("\n8. ข้ามภาพนุ่ม ๆ กับตัดแข็ง ใช้คำสั่งคนละแบบ")
        rec = render.ff.run = Recorder()
        render.build_visual(P("a.jpg", "b.jpg", "c.jpg"), 9.0, Path("."), style="clean")
        joined = rec.joined()
        check("แนวเรียบใช้ xfade", "xfade=transition=fade" in joined, True)
        check("ไม่ใช้ concat แล้ว", "-f concat" in joined, False)
        # ยืดช็อตชดเชยเวลาที่หายไปตอนภาพซ้อน ผลรวมจึงยังเท่าเป้า
        check("ช็อตถูกยืดชดเชย", "-t 3.200" in joined, True)
        check("ช็อตที่สองเริ่มซ้อนตรงเวลา", "offset=2.900" in joined, True)

        rec = render.ff.run = Recorder()
        render.build_visual(P("a.jpg", "b.jpg", "c.jpg"), 9.0, Path("."), style="warehouse")
        joined = rec.joined()
        check("แนวโกดังตัดแข็ง ไม่มี xfade", "xfade" in joined, False)
        check("ตัดแข็งต่อไฟล์ตรง ๆ", "-f concat" in joined, True)
        check("ไม่ยืดช็อต ตัดแข็งไม่มีเวลาหาย", "-t 3.000" in joined, True)

        print("\n9. สายพาน")
        rec = render.ff.run = Recorder()
        render.build_visual(P("a.jpg", "b.jpg", "c.jpg"), 12.0, Path("."), style="conveyor")
        check("ยิง ffmpeg ครั้งเดียว ไม่ตัดเป็นช็อต", len(rec.calls), 1)
        joined = rec.joined()
        check("ไม่มี xfade และไม่มี concat",
              "xfade" in joined or "-f concat" in joined, False)
        check("ต่อการ์ดไว้สองชุดเพื่อให้วนได้ไม่มีรอยต่อ", "hstack=inputs=6" in joined, True)
        check("การ์ดทุกใบถูก split", joined.count("split=2"), 3)
        check("เลื่อนด้วย mod เพื่อวนลูป", "-mod(t*" in joined, True)
        check("ระยะหนึ่งรอบ = การ์ดกว้าง x จำนวนการ์ด", f",{842 * 3})" in joined, True)
        check("ใส่รูปครบทุกใบ", joined.count("-loop 1"), 3)
        check("คุมความยาวปลายทาง", "-t 12.000" in joined, True)

        print("\n10. กรณีขอบ")
        rec = render.ff.run = Recorder()
        render.build_visual(P("a.jpg"), 6.0, Path("."), style="clean")
        check("ช็อตเดียว ไม่ต้องข้ามภาพ", "xfade" in rec.joined(), False)

        rec = render.ff.run = Recorder()
        render.build_visual(P("a.jpg"), 6.0, Path("."), style="conveyor")
        check("สายพานการ์ดใบเดียวก็ยังวนได้", "hstack=inputs=2" in rec.joined(), True)

        try:
            render.build_visual([], 6.0, Path("."), style="clean")
            check("ไม่มีช็อตต้องโยน error", False, True)
        except ValueError:
            check("ไม่มีช็อตต้องโยน error", True, True)

        rec = render.ff.run = Recorder()
        render.build_visual(P("a.jpg", "b.jpg"), 8.0, Path("."), style="ไม่มีแนวนี้")
        check("แนวที่ไม่รู้จัก ยังเรนเดอร์ได้ด้วยแนวเรียบ",
              "xfade=transition=fade" in rec.joined(), True)

        rec = render.ff.run = Recorder()
        render.build_visual(P("a.jpg", "b.jpg"), 8.0, Path("."), style=style.get("conveyor"))
        check("ส่ง Style object มาตรง ๆ ก็ได้", "hstack" in rec.joined(), True)
    finally:
        render.ff.run = original

    print(f"\nผ่าน {PASS} · ไม่ผ่าน {FAIL}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
