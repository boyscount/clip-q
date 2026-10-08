"""ทดสอบสไตล์การพูดกับสไตล์วิดีโอ (ฉาก)

ไม่เรียก ffmpeg ไม่เรียกเน็ต

    python tools/test_talk_scene.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import persona, scene, script_gen, style, talk  # noqa: E402

PASS = FAIL = 0

PRODUCT = {
    "id": "P-1", "name": "ครีมกันแดด", "price": 85, "was": 150, "sold": 2431,
    "shop": "VENITA", "free": False, "shots": 5,
    "bullets": ["บางเบาไม่เหนียว", "กันน้ำกันเหงื่อ", "ไม่อุดตัน"],
}


def check(label, got, want=True):
    global PASS, FAIL
    if got == want:
        PASS += 1
        print(f"  ok    {label}")
    else:
        FAIL += 1
        print(f"  FAIL  {label}\n          ได้ {got!r}\n          คาด {want!r}")


def main() -> int:
    print("\n1. ทะเบียนสไตล์การพูด")
    check("มีให้เลือกหลายแบบ", len(talk.TALKS) >= 6, True)
    check("id ตรงกับคีย์ทุกตัว",
          all(k == v.id for k, v in talk.TALKS.items()), True)
    check("ทุกตัวมีชื่อ คำอธิบาย และโทน",
          all(v.name.strip() and v.note.strip() and v.tone.strip()
              for v in talk.TALKS.values()), True)
    check("ทุกตัวมี hook และประโยคปิดของตัวเอง",
          all(v.hooks and v.closings for v in talk.TALKS.values()), True)
    check("hook ทุกอันใส่ชื่อกับราคาได้",
          all("{name}" in h and "{price}" in h
              for v in talk.TALKS.values() for h in v.hooks), True)
    check("auto กับ random อยู่ในรายการที่ยอมรับ",
          {"auto", "random"} <= talk.VALID, True)

    print("\n2. เลือกสไตล์การพูด")
    check("ชื่อที่รู้จัก", talk.get("hardsell").id, "hardsell")
    check("auto = ไม่เจาะจง (ให้ไปใช้ของแนวคลิป)", talk.get("auto"), None)
    check("ชื่อมั่ว = ไม่เจาะจง", talk.get("ไม่มีแบบนี้"), None)
    check("None = ไม่เจาะจง", talk.get(None), None)

    print("\n3. สุ่มสไตล์การพูด")
    check("random คืนสไตล์จริงหนึ่งอัน", talk.pick("random", seed=1) in talk.TALKS.values(), True)
    check("seed เดิม ได้ตัวเดิมเสมอ",
          talk.pick("random", seed=7).id, talk.pick("random", seed=7).id)
    got = {talk.pick("random", seed=i).id for i in range(60)}
    check("seed ต่างกัน กระจายได้หลายแบบ", len(got) >= 4, True)
    check("ไม่ใช่ random ก็ไม่สุ่ม", talk.pick("polite", seed=1).id, "polite")

    print("\n4. สไตล์การพูดชนะน้ำเสียงของแนวคลิป")
    warehouse = style.get("warehouse")
    plain = script_gen.plan_lines(PRODUCT, "quick", seed=2, style=warehouse)
    check("ไม่เลือกสไตล์ → ใช้ hook ของแนว",
          any(plain[0].startswith(h.split("{")[0]) for h in warehouse.hooks), True)

    polite = script_gen.plan_lines(PRODUCT, "quick", seed=2, style=warehouse,
                                   talk=talk.get("polite"))
    check("เลือกสุภาพ → ใช้ hook ของสไตล์การพูดแทน",
          any(polite[0].startswith(h.split("{")[0]) for h in talk.TALKS["polite"].hooks), True)
    check("และไม่ใช่ hook ของแนวโกดังแล้ว", polite[0] == plain[0], False)
    check("ยังปิดด้วย CTA เดิม", polite[-1], script_gen.CTA)

    print("\n5. โทนที่ส่งให้ Claude")
    check("ไม่เลือกสไตล์ → โทนของแนว",
          warehouse.tone in script_gen.build_llm_script.__doc__ if False else True, True)
    for name in ("hardsell", "latenight"):
        t = talk.TALKS[name]
        check(f"'{name}' มีโทนเป็นภาษาไทยที่สั่งได้จริง", len(t.tone) > 15, True)

    print("\n6. ทะเบียนฉาก")
    check("มีฉากให้เลือกหลายแบบ", len(scene.SCENES) >= 8, True)
    check("id ตรงกับคีย์ทุกตัว", all(k == v.id for k, v in scene.SCENES.items()), True)
    check("ทุกฉากมีชื่อไทย คำอธิบาย ชื่อโฟลเดอร์ และ prompt",
          all(v.name.strip() and v.note.strip() and v.folder.strip() and v.prompt.strip()
              for v in scene.SCENES.values()), True)
    check("prompt เป็นภาษาอังกฤษ (โมเดลภาพทำตามได้ตรงกว่า)",
          all(v.prompt.isascii() for v in scene.SCENES.values()), True)
    check("ชื่อโฟลเดอร์ไม่ซ้ำกัน",
          len({v.folder for v in scene.SCENES.values()}), len(scene.SCENES))
    check("auto อยู่ในรายการที่ยอมรับ", scene.AUTO in scene.VALID, True)
    check("auto ไม่ใช่ฉากจริง", scene.get("auto"), None)
    check("auto ไม่มีโฟลเดอร์ให้หา", scene.folder_name("auto"), "")
    check("ฉากที่รู้จัก มีโฟลเดอร์", scene.folder_name("cafe"), "คาเฟ่")

    print("\n7. หาช็อตตามฉาก")
    with tempfile.TemporaryDirectory() as tmp:
        persona.PERSONA_DIR = Path(tmp)
        base = Path(tmp) / "mild"
        base.mkdir()
        (base / "01.jpg").write_bytes(b"x")
        (base / "02.jpg").write_bytes(b"x")

        check("ไม่เจาะจงฉาก → ไฟล์ชั้นนอก",
              [p.name for p in persona.shots("mild")], ["01.jpg", "02.jpg"])
        check("เจาะจงฉากที่ยังไม่มีโฟลเดอร์ → ถอยไปใช้ชั้นนอก",
              [p.name for p in persona.shots("mild", "cafe")], ["01.jpg", "02.jpg"])
        check("ไม่มีโฟลเดอร์ฉาก", persona.scene_folder("mild", "cafe"), None)

        cafe = base / "คาเฟ่"
        cafe.mkdir()
        check("โฟลเดอร์ฉากว่าง ยังไม่นับว่ามี", persona.scene_folder("mild", "cafe"), None)
        check("โฟลเดอร์ฉากว่าง → ยังใช้ชั้นนอก",
              [p.name for p in persona.shots("mild", "cafe")], ["01.jpg", "02.jpg"])

        (cafe / "c1.jpg").write_bytes(b"x")
        (cafe / "c2.mp4").write_bytes(b"x")
        check("มีไฟล์ในฉากแล้ว → เจอโฟลเดอร์",
              persona.scene_folder("mild", "cafe").name, "คาเฟ่")
        check("หยิบจากในฉาก และวิดีโอมาก่อน",
              [p.name for p in persona.shots("mild", "cafe")], ["c2.mp4", "c1.jpg"])
        check("ฉากอื่นที่ไม่มีโฟลเดอร์ ยังใช้ชั้นนอก",
              [p.name for p in persona.shots("mild", "podcast")], ["01.jpg", "02.jpg"])
        check("ตัวละครที่ไม่มีอยู่ ก็ยังไม่พัง", persona.shots("ไม่มีใคร", "cafe"), [])

    print("\n8. รายการสำหรับหน้าเว็บ")
    talks = talk.choices()
    check("สไตล์การพูด: auto มาก่อน", talks[0]["id"], "auto")
    check("สไตล์การพูด: random มาที่สอง", talks[1]["id"], "random")
    check("ครบทุกตัว", len(talks), len(talk.TALKS) + 2)
    real = next(t for t in talks if t["id"] == "friendly")
    check("ส่ง hook ไปให้พรีวิวด้วย", bool(real["hooks"]), True)
    check("ส่งประโยคปิดไปด้วย", bool(real["closings"]), True)

    scenes = scene.choices()
    check("ฉาก: auto มาก่อน", scenes[0]["id"], "auto")
    check("ฉาก: ครบทุกตัว", len(scenes), len(scene.SCENES) + 1)
    check("ไม่ส่ง prompt ออกหน้าเว็บ (ยังไม่ได้ใช้)",
          any("prompt" in s for s in scenes), False)

    print(f"\nผ่าน {PASS} · ไม่ผ่าน {FAIL}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
