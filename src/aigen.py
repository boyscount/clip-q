"""โมเดล AI ของ Google สำหรับสร้างช็อตคน — ทะเบียนรุ่นและการตั้งค่า

**Google Flow เองไม่มี API ให้เรียก** Flow คือเว็บแอปตัดต่อของ Google ที่ใช้ Veo
อยู่ข้างใน ถ้าจะให้ ClipQueue สร้างภาพเองต้องเรียกผ่าน Gemini API ซึ่งเป็นคนละ
ทางเข้าแต่ใช้โมเดลตระกูลเดียวกัน ที่นี่จึงตั้งค่าเป็น Gemini API

ตั้งค่าใน .env

    GOOGLE_API_KEY       คีย์จาก Google AI Studio
    GOOGLE_IMAGE_MODEL   รุ่นที่ใช้สร้างภาพนิ่ง  (ไม่ใส่ = ค่าตั้งต้นด้านล่าง)
    GOOGLE_VIDEO_MODEL   รุ่นที่ใช้สร้างวิดีโอสั้น

รายชื่อรุ่นด้านล่างอ้างจากเอกสาร Gemini API ตอนตุลาคม 2026 — Google ปลดรุ่นเก่า
ออกค่อนข้างเร็ว (Imagen 4 ปิดไปเมื่อสิงหาคม 2026, Veo 2.0 กับ 3.0 ปิดมิถุนายน
2026) ถ้ารุ่นไหนเรียกแล้วได้ 404 ให้มาแก้ที่ไฟล์นี้ไฟล์เดียว

ยังไม่มีการเรียก API จริงในไฟล์นี้ — ส่วนนี้คือทะเบียนรุ่นกับการตั้งค่าเท่านั้น
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Model:
    id: str
    name: str
    note: str


# ---------------------------------------------------------------- ภาพนิ่ง
IMAGE_MODELS: tuple[Model, ...] = (
    Model("gemini-3-pro-image", "Nano Banana Pro",
          "คุณภาพสูงสุด · คุมหน้าให้เหมือนเดิมได้ดีที่สุด · แพงและช้ากว่า"),
    Model("gemini-3.1-flash-image", "Nano Banana 2",
          "สมดุลราคากับคุณภาพ · เหมาะกับงานประจำวัน"),
    Model("gemini-3.1-flash-lite-image", "Flash Lite",
          "ถูกและเร็วที่สุด · เหมาะกับลองไอเดียก่อนสั่งตัวจริง"),
    Model("gemini-2.5-flash-image", "Flash 2.5 (รุ่นเก่า)",
          "รุ่นก่อนหน้า · ใช้ได้ถ้าของใหม่ยังไม่เปิดให้บัญชีคุณ"),
)

# ---------------------------------------------------------------- วิดีโอ
VIDEO_MODELS: tuple[Model, ...] = (
    Model("veo-3.1-generate-preview", "Veo 3.1",
          "คุณภาพสูงสุด · ภาพคนเคลื่อนไหวเนียนที่สุด · แพงที่สุดต่อวินาที"),
    Model("veo-3.1-fast-generate-preview", "Veo 3.1 Fast",
          "เร็วกว่าและถูกกว่า · คุณภาพลดลงเล็กน้อย"),
    Model("veo-3.1-lite-generate-preview", "Veo 3.1 Lite",
          "ถูกที่สุด · เหมาะกับช็อตสั้นที่ไม่ใช่พระเอกของคลิป"),
)

DEFAULT_IMAGE = "gemini-3.1-flash-image"
DEFAULT_VIDEO = "veo-3.1-fast-generate-preview"


def _pick(models: tuple[Model, ...], wanted: str | None, fallback: str) -> str:
    """รุ่นที่ขอ ถ้าไม่รู้จักให้ถอยไปค่าตั้งต้น

    ชื่อรุ่นที่พิมพ์ผิดใน .env ไม่ควรทำให้แอปไม่ขึ้น แต่ควรเห็นได้จาก status()
    """
    ids = {m.id for m in models}
    wanted = (wanted or "").strip()
    return wanted if wanted in ids else fallback


def image_model() -> str:
    return _pick(IMAGE_MODELS, os.environ.get("GOOGLE_IMAGE_MODEL"), DEFAULT_IMAGE)


def video_model() -> str:
    return _pick(VIDEO_MODELS, os.environ.get("GOOGLE_VIDEO_MODEL"), DEFAULT_VIDEO)


def has_key() -> bool:
    return bool((os.environ.get("GOOGLE_API_KEY") or "").strip())


def choices() -> dict:
    """ทุกอย่างที่หน้าเว็บต้องใช้วาดหน้า Settings"""
    return {
        "image": [{"id": m.id, "name": m.name, "note": m.note} for m in IMAGE_MODELS],
        "video": [{"id": m.id, "name": m.name, "note": m.note} for m in VIDEO_MODELS],
        "imageModel": image_model(),
        "videoModel": video_model(),
        "hasKey": has_key(),
    }


def status() -> dict:
    """สรุปสถานะไว้ให้ doctor และหน้า Settings อ่านตรงกัน"""
    requested_image = (os.environ.get("GOOGLE_IMAGE_MODEL") or "").strip()
    requested_video = (os.environ.get("GOOGLE_VIDEO_MODEL") or "").strip()
    unknown = [
        name for name, wanted, chosen in (
            ("GOOGLE_IMAGE_MODEL", requested_image, image_model()),
            ("GOOGLE_VIDEO_MODEL", requested_video, video_model()),
        ) if wanted and wanted != chosen
    ]
    return {
        "hasKey": has_key(),
        "imageModel": image_model(),
        "videoModel": video_model(),
        "unknownModels": unknown,
        "ready": has_key() and not unknown,
    }
