"""ทดสอบทะเบียนโมเดล AI ของ Google และการอ่านค่าจาก .env

ไม่เรียก API จริง ไม่ต้องมีคีย์

    python tools/test_aigen.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import aigen  # noqa: E402

PASS = FAIL = 0


def check(label, got, want=True):
    global PASS, FAIL
    if got == want:
        PASS += 1
        print(f"  ok    {label}")
    else:
        FAIL += 1
        print(f"  FAIL  {label}\n          ได้ {got!r}\n          คาด {want!r}")


def env(**over):
    """ตั้งค่า env ชั่วคราว ค่า None = ถอดตัวแปรนั้นออก"""
    for key, value in over.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def main() -> int:
    saved = {k: os.environ.get(k) for k in
             ("GOOGLE_API_KEY", "GOOGLE_IMAGE_MODEL", "GOOGLE_VIDEO_MODEL")}
    try:
        print("\n1. ทะเบียนรุ่น")
        check("มีรุ่นภาพให้เลือก", len(aigen.IMAGE_MODELS) >= 3, True)
        check("มีรุ่นวิดีโอให้เลือก", len(aigen.VIDEO_MODELS) >= 3, True)
        for group, models in (("ภาพ", aigen.IMAGE_MODELS), ("วิดีโอ", aigen.VIDEO_MODELS)):
            ids = [m.id for m in models]
            check(f"รุ่น{group}ไม่มี id ซ้ำ", len(ids), len(set(ids)))
            check(f"รุ่น{group}มีชื่อและคำอธิบายครบ",
                  all(m.name.strip() and m.note.strip() for m in models), True)
        check("ค่าตั้งต้นภาพอยู่ในทะเบียน",
              aigen.DEFAULT_IMAGE in {m.id for m in aigen.IMAGE_MODELS}, True)
        check("ค่าตั้งต้นวิดีโออยู่ในทะเบียน",
              aigen.DEFAULT_VIDEO in {m.id for m in aigen.VIDEO_MODELS}, True)

        print("\n2. รุ่นที่ปลดระวางไปแล้ว ต้องไม่อยู่ในรายการ")
        # Imagen 4 ปิดสิงหาคม 2026 · Veo 2.0 กับ 3.0 ปิดมิถุนายน 2026
        gone = {"imagen-4.0-generate-001", "imagen-4.0-ultra-generate-001",
                "imagen-4.0-fast-generate-001", "veo-2.0-generate-001",
                "veo-3.0-generate-001", "veo-3.0-fast-generate-001"}
        live = {m.id for m in aigen.IMAGE_MODELS} | {m.id for m in aigen.VIDEO_MODELS}
        check("ไม่มีรุ่นที่ Google ปิดไปแล้ว", sorted(live & gone), [])

        print("\n3. อ่านค่าจาก .env")
        env(GOOGLE_IMAGE_MODEL=None, GOOGLE_VIDEO_MODEL=None, GOOGLE_API_KEY=None)
        check("ไม่ตั้งอะไร → ค่าตั้งต้น", aigen.image_model(), aigen.DEFAULT_IMAGE)
        check("ไม่ตั้งอะไร → ค่าตั้งต้นวิดีโอ", aigen.video_model(), aigen.DEFAULT_VIDEO)
        check("ไม่มีคีย์", aigen.has_key(), False)

        env(GOOGLE_IMAGE_MODEL="gemini-3-pro-image",
            GOOGLE_VIDEO_MODEL="veo-3.1-generate-preview")
        check("ตั้งรุ่นภาพที่มีจริง", aigen.image_model(), "gemini-3-pro-image")
        check("ตั้งรุ่นวิดีโอที่มีจริง", aigen.video_model(), "veo-3.1-generate-preview")

        env(GOOGLE_IMAGE_MODEL="  gemini-3-pro-image  ")
        check("มีช่องว่างหน้าหลังก็ยังเจอ", aigen.image_model(), "gemini-3-pro-image")

        print("\n4. ชื่อรุ่นที่พิมพ์ผิด ต้องไม่ทำให้แอปล่ม")
        env(GOOGLE_IMAGE_MODEL="imagen-9-ไม่มีจริง", GOOGLE_VIDEO_MODEL="veo-99")
        check("รุ่นภาพมั่ว → ถอยไปค่าตั้งต้น", aigen.image_model(), aigen.DEFAULT_IMAGE)
        check("รุ่นวิดีโอมั่ว → ถอยไปค่าตั้งต้น", aigen.video_model(), aigen.DEFAULT_VIDEO)
        st = aigen.status()
        check("แต่ status บอกว่าชื่อไหนผิด",
              st["unknownModels"], ["GOOGLE_IMAGE_MODEL", "GOOGLE_VIDEO_MODEL"])
        check("และยังไม่พร้อมใช้", st["ready"], False)

        print("\n5. คีย์")
        env(GOOGLE_IMAGE_MODEL=None, GOOGLE_VIDEO_MODEL=None, GOOGLE_API_KEY="   ")
        check("คีย์ที่มีแต่ช่องว่าง = ยังไม่มีคีย์", aigen.has_key(), False)
        env(GOOGLE_API_KEY="AIza-ทดสอบ")
        check("มีคีย์", aigen.has_key(), True)
        check("มีคีย์ + รุ่นถูก = พร้อม", aigen.status()["ready"], True)

        print("\n6. รายการที่ส่งให้หน้าเว็บ")
        c = aigen.choices()
        check("มีครบทุกคีย์", sorted(c),
              ["hasKey", "image", "imageModel", "video", "videoModel"])
        check("จำนวนรุ่นภาพตรงกับทะเบียน", len(c["image"]), len(aigen.IMAGE_MODELS))
        check("แต่ละรุ่นมี id name note", sorted(c["image"][0]), ["id", "name", "note"])
        check("บอกรุ่นที่เลือกอยู่ด้วย", c["imageModel"], aigen.image_model())
        check("ไม่ส่งคีย์ออกไปให้หน้าเว็บ",
              any("AIza" in str(v) for v in c.values()), False)
    finally:
        for key, value in saved.items():
            env(**{key: value})

    print(f"\nผ่าน {PASS} · ไม่ผ่าน {FAIL}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
