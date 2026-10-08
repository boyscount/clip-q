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


# ------------------------------------------------------------- สร้างภาพจริง

ENDPOINT = ("https://generativelanguage.googleapis.com/v1beta/models/"
            "{model}:generateContent")
TIMEOUT = 180
MAX_BATCH = 4          # กันกดรัวแล้วบิลบาน
IMAGE_SUFFIX = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}

# กติกาที่ทุก prompt ต้องมี ไม่ว่าฉากไหน — คลิปเป็นแนวตั้งและมีป้ายราคากับซับ
# ทับอยู่ ภาพที่มีตัวหนังสือของตัวเองจะชนกันเละ
FRAMING = ("vertical 9:16 portrait orientation, photorealistic, "
           "no text or watermarks or logos in the image, "
           "leave headroom at the top and empty space at the bottom")


class GenError(RuntimeError):
    """ข้อความที่เอาไปโชว์ผู้ใช้ได้ตรง ๆ"""


def build_prompt(scene_id: str | None = None, look: str = "",
                 product: str = "") -> str:
    """ประกอบคำสั่งจากฉาก รูปลักษณ์ตัวละคร และสินค้าที่ถือ

    เขียนเป็นอังกฤษเพราะโมเดลภาพทำตามได้ตรงกว่าไทย ส่วนที่ผู้ใช้พิมพ์เองจะถูก
    ต่อไว้ตรง ๆ โดยไม่แปล — ใครอยากสั่งเป็นไทยก็ยังทำได้
    """
    from . import scene as scenes

    bits = [look.strip() or "a friendly Thai person in their twenties"]
    if product.strip():
        bits.append(f"holding {product.strip()} toward the camera")
    picked = scenes.get(scene_id)
    if picked:
        bits.append(picked.prompt)
    bits.append(FRAMING)
    return ", ".join(bits)


def _parts(payload: dict) -> list[dict]:
    candidates = payload.get("candidates") or []
    if not candidates:
        raise GenError("โมเดลไม่ได้ส่งผลลัพธ์กลับมา")
    first = candidates[0]
    reason = first.get("finishReason")
    content = first.get("content") or {}
    parts = content.get("parts") or []
    if not parts and reason:
        raise GenError(f"โมเดลหยุดกลางคัน ({reason}) — ลองแก้คำสั่งแล้วสั่งใหม่")
    return parts


def generate_images(prompt: str, count: int = 1, model: str | None = None,
                    session=None) -> list[tuple[bytes, str]]:
    """สร้างภาพตามคำสั่ง คืน [(ข้อมูลไฟล์, นามสกุล), ...]

    ยิงทีละภาพ ไม่ใช่ขอหลายภาพในครั้งเดียว เพราะถ้าโควตาหมดกลางทางจะได้ภาพที่
    สร้างสำเร็จไปแล้วเก็บไว้ ไม่เสียเงินฟรี
    """
    import requests

    if not has_key():
        raise GenError("ยังไม่ได้ตั้ง GOOGLE_API_KEY ใน .env")
    count = max(1, min(MAX_BATCH, int(count)))
    model = model or image_model()
    http = session or requests
    url = ENDPOINT.format(model=model)
    headers = {"x-goog-api-key": os.environ["GOOGLE_API_KEY"].strip(),
               "Content-Type": "application/json"}
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseModalities": ["IMAGE"]},
    }

    out: list[tuple[bytes, str]] = []
    for _ in range(count):
        try:
            resp = http.post(url, headers=headers, json=body, timeout=TIMEOUT)
        except Exception as exc:  # noqa: BLE001
            if out:
                break           # ได้มาบ้างแล้ว เก็บไว้ดีกว่าทิ้งทั้งหมด
            raise GenError(f"ต่อ Google ไม่ได้ · {exc}") from exc

        if resp.status_code != 200:
            if out:
                break
            raise GenError(_explain(resp, model))

        found = False
        for part in _parts(resp.json()):
            blob = part.get("inlineData") or part.get("inline_data")
            if not blob:
                continue
            import base64
            mime = blob.get("mimeType") or blob.get("mime_type") or "image/png"
            out.append((base64.b64decode(blob["data"]), IMAGE_SUFFIX.get(mime, ".png")))
            found = True
        if not found and not out:
            raise GenError("โมเดลตอบกลับมาแต่ไม่มีรูป — ลองเปลี่ยนคำสั่งให้ชัดขึ้น")
    return out


def _explain(resp, model: str) -> str:
    """แปล error ของ Google เป็นคำที่บอกได้ว่าต้องไปทำอะไรต่อ"""
    try:
        message = (resp.json().get("error") or {}).get("message", "")
    except Exception:  # noqa: BLE001
        message = resp.text[:200]

    if resp.status_code == 429 and "limit: 0" in message:
        return (f"บัญชี Google ยังไม่เปิด billing จึงสร้างภาพไม่ได้เลย"
                f" (โควตาฟรีของ {model} เป็น 0) — เปิด billing ในโปรเจกต์"
                f" Google Cloud ที่ผูกกับคีย์นี้ก่อน")
    if resp.status_code == 429:
        return f"ยิงถี่เกินโควตา ลองใหม่อีกสักครู่ · {message[:160]}"
    if resp.status_code in (401, 403):
        return f"คีย์ไม่มีสิทธิ์เรียก {model} · {message[:160]}"
    if resp.status_code == 400:
        return f"คำสั่งไม่ถูกรูปแบบ · {message[:160]}"
    return f"Google ตอบ HTTP {resp.status_code} · {message[:160]}"


def save_shots(persona_name: str, scene_id: str | None, images,
               prefix: str = "ai") -> list:
    """เซฟภาพที่สร้างได้ลงโฟลเดอร์ตัวละคร (แยกตามฉากถ้าเลือกฉากไว้)

    ตั้งชื่อขึ้นต้นด้วย ai_ เพื่อให้แยกออกจากรูปที่ถ่ายเองได้ด้วยตาเปล่า และ
    ไล่เลขต่อจากไฟล์ที่มีอยู่ จะได้ไม่ทับของเดิม
    """
    from . import persona, scene as scenes

    base = persona.folder_for(persona_name)
    if base is None:
        base = persona.PERSONA_DIR / persona_name.strip()
        base.mkdir(parents=True, exist_ok=True)

    folder = scenes.folder_name(scene_id)
    target = (base / folder) if folder else base
    target.mkdir(parents=True, exist_ok=True)

    used = {p.stem for p in target.iterdir() if p.is_file()}
    saved = []
    n = 1
    for data, suffix in images:
        while f"{prefix}_{n:02d}" in used:
            n += 1
        path = target / f"{prefix}_{n:02d}{suffix}"
        path.write_bytes(data)
        used.add(path.stem)
        saved.append(path)
    return saved
