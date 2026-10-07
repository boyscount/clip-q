"""ช็อตคนสำหรับแทรกในคลิป

วางไฟล์ไว้ที่ `app/assets/personas/<ชื่อตัวละคร>/` — รูป (.jpg/.png) หรือคลิปสั้น
(.mp4/.mov) ก็ได้ ตั้งชื่อเรียงกัน 01, 02, … ระบบจะหยิบตามลำดับ

    app/assets/personas/
      น้องมายด์ สายบิวตี้/
        01.jpg      ถือสินค้าให้เห็นหน้า
        02.mp4      ทาแล้วยิ้ม 5 วินาที
        03.jpg      ชี้ป้ายราคา

ไม่มีโฟลเดอร์ = ไม่มีช็อตคน คลิปก็ยังเรนเดอร์ได้ตามเดิมด้วยภาพสินค้าล้วน
"""

from __future__ import annotations

import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PERSONA_DIR = ROOT / "app" / "assets" / "personas"

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".webm"}
MEDIA_SUFFIXES = IMAGE_SUFFIXES | VIDEO_SUFFIXES

# ชื่อที่แปลว่า "ไม่ต้องใส่คน"
NONE_NAMES = {"", "ไม่ใช้ตัวละคร", "none", "-"}

_UNSAFE = re.compile(r"[^0-9A-Za-z฀-๿]+")

# สัดส่วนช็อตคนต่อช็อตทั้งหมด ปรับได้ด้วย CLIPQUEUE_PERSON_SHARE (0.0-0.8)
PERSON_SHARE = min(0.8, max(0.0, float(os.environ.get("CLIPQUEUE_PERSON_SHARE", "0.55"))))


def slug(name: str) -> str:
    return _UNSAFE.sub("-", name.strip()).strip("-").lower()


def folder_for(name: str) -> Path | None:
    """หาโฟลเดอร์ของตัวละคร — ยอมรับทั้งชื่อตรง ๆ และชื่อที่ถูกแปลงแล้ว

    คนตั้งชื่อโฟลเดอร์ด้วยมือ จึงไม่ควรบังคับรูปแบบเดียว
    """
    if not name or name.strip() in NONE_NAMES:
        return None
    if not PERSONA_DIR.is_dir():
        return None

    exact = PERSONA_DIR / name.strip()
    if exact.is_dir():
        return exact

    target = slug(name)
    for child in PERSONA_DIR.iterdir():
        if child.is_dir() and slug(child.name) == target:
            return child
    return None


def shots(name: str) -> list[Path]:
    """ไฟล์สื่อของตัวละคร เรียงตามชื่อไฟล์"""
    folder = folder_for(name)
    if folder is None:
        return []
    files = sorted(
        p for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() in MEDIA_SUFFIXES
    )
    # วิดีโอมาก่อนรูปนิ่ง: เมื่อช่องสำหรับคนมีจำกัด ช็อตที่ขยับจริงกินใจกว่าภาพนิ่ง
    return sorted(files, key=lambda f: (not is_video(f),))


def is_video(path: Path) -> bool:
    return path.suffix.lower() in VIDEO_SUFFIXES


def interleave(product_shots: list[Path], persona_shots: list[Path],
               count: int | None = None) -> list[Path]:
    """เรียงช็อตให้คนเปิดและปิดคลิป ส่วนกลางเป็นสินค้า

    เปิดด้วยหน้าคนเพราะคนดูหยุดนิ้วกับหน้าคนมากกว่ากล่องสินค้า และปิดด้วยคน
    ตอนพูด CTA เพราะเป็นจังหวะที่ขอให้กดตะกร้า ตรงกลางปล่อยให้สินค้าเล่าตัวเอง
    """
    if not product_shots:
        return list(persona_shots)
    if not persona_shots:
        return list(product_shots)

    total = max(2, count or len(product_shots))

    # สัดส่วนคนตาม PERSON_SHARE แต่ไม่เกินจำนวนไฟล์ที่มีจริง และต้องเหลือ
    # ช่องให้สินค้าอย่างน้อยหนึ่งช็อต — คลิปที่ไม่เห็นสินค้าเลยขายไม่ได้
    want_person = min(len(persona_shots), total - 1, max(1, round(total * PERSON_SHARE)))

    slots = {0}                                   # ช็อตแรกเป็นคนเสมอ
    if want_person > 1:
        slots.add(total - 1)                      # ช็อตปิดท้ายตอนพูด CTA
    for i in range(1, want_person - len(slots) + 1):
        # ที่เหลือกระจายตรงกลาง ไม่ชิดหัวท้าย
        step = total / (want_person - len(slots) + 2)
        slots.add(min(total - 2, max(1, round(i * step))))

    plan: list[Path] = []
    person = product = 0
    for slot in range(total):
        if slot in slots:
            plan.append(persona_shots[person % len(persona_shots)])
            person += 1
        else:
            plan.append(product_shots[product % len(product_shots)])
            product += 1
    return plan
