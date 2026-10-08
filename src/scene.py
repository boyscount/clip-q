"""สไตล์วิดีโอ — ฉากที่ช็อตคนอยู่

ฉากไม่ได้ถูกวาดขึ้นมาเอง ภาพมาจากไฟล์ที่วางไว้เสมอ ฉากทำสองอย่าง

1. **เลือกโฟลเดอร์** ถ้ามี `app/assets/personas/<ตัวละคร>/<ฉาก>/` ก็หยิบจาก
   ในนั้น ไม่มีก็ถอยไปใช้ไฟล์ที่อยู่ชั้นนอกตามเดิม — จัดชุดช็อตตามฉากได้
   โดยไม่ต้องมีคีย์ AI และไม่ต้องแก้โค้ด

       app/assets/personas/mild/
         01.jpg                    ← ใช้เมื่อฉากไม่มีโฟลเดอร์ของตัวเอง
         คาเฟ่/01.jpg 02.mp4
         สตูดิโอขาว/01.jpg

2. **เป็นคำสั่งให้ AI** `prompt` คือคำบรรยายฉากเป็นภาษาอังกฤษ ไว้ส่งให้ Veo
   หรือ Nano Banana ตอนต่อปุ่มสร้างภาพ (ดู `aigen.py`) ยังไม่ถูกเรียกตอนนี้

prompt เขียนเป็นภาษาอังกฤษเพราะโมเดลภาพยังทำตามคำสั่งอังกฤษได้ตรงกว่าไทย
ส่วนชื่อกับคำอธิบายเป็นไทยเพราะคนอ่านคือคนไทย
"""

from __future__ import annotations

import re
from dataclasses import dataclass

AUTO = "auto"

_UNSAFE = re.compile(r"[^0-9A-Za-z฀-๿]+")


@dataclass(frozen=True)
class Scene:
    id: str
    name: str
    note: str
    folder: str      # ชื่อโฟลเดอร์ย่อยที่จะไปหาช็อต
    prompt: str      # คำบรรยายฉากสำหรับโมเดลภาพ/วิดีโอ


SCENES: dict[str, Scene] = {
    "podcast": Scene(
        "podcast", "สตูดิโอ Podcast",
        "นั่งโต๊ะมีไมค์ ไฟนุ่ม · ดูเป็นผู้รู้ เหมาะกับของที่ต้องอธิบาย",
        "สตูดิโอพอดแคสต์",
        "podcast studio, person seated at a desk with a microphone, warm key light,"
        " soft bokeh background, vertical framing",
    ),
    "sofa": Scene(
        "sofa", "นั่งโซฟา",
        "ห้องนั่งเล่น ผ่อนคลาย · เหมาะกับของใช้ในบ้าน",
        "โซฟา",
        "cozy living room, person sitting on a sofa holding the product,"
        " natural window light, vertical framing",
    ),
    "ugc": Scene(
        "ugc", "UGC รีวิว",
        "ถือมือถือถ่ายเอง ภาพสั่นนิด ๆ · ดูจริงที่สุด แปลงเป็นยอดดีที่สุด",
        "ยูจีซี",
        "handheld selfie-style user generated review, slight camera shake,"
        " everyday room lighting, vertical phone framing",
    ),
    "cafe": Scene(
        "cafe", "คาเฟ่",
        "โต๊ะคาเฟ่ ฉากหลังเบลอ · ดูมีไลฟ์สไตล์",
        "คาเฟ่",
        "cafe table by a window, blurred background with plants,"
        " soft daylight, vertical framing",
    ),
    "outdoor": Scene(
        "outdoor", "กลางแจ้ง",
        "แสงธรรมชาติ กลางวัน · เหมาะกับของกีฬา ของพกพา",
        "กลางแจ้ง",
        "outdoors in daylight, natural sunlight, park or street background,"
        " vertical framing",
    ),
    "whitestudio": Scene(
        "whitestudio", "สตูดิโอขาว",
        "พื้นหลังขาวสะอาด · เน้นตัวสินค้า เหมาะกับสกินแคร์ เครื่องสำอาง",
        "สตูดิโอขาว",
        "clean white studio backdrop, even softbox lighting, product held up"
        " to camera, vertical framing",
    ),
    "desk": Scene(
        "desk", "โต๊ะทำงาน",
        "โต๊ะคอม มีของวางรอบ · เหมาะกับแกดเจ็ต ของใช้สำนักงาน",
        "โต๊ะทำงาน",
        "home office desk setup, monitor glow and desk lamp,"
        " person showing the product, vertical framing",
    ),
    "vanity": Scene(
        "vanity", "โต๊ะเครื่องแป้ง",
        "กระจกแต่งหน้า ไฟรอบกระจก · เหมาะกับสกินแคร์ เครื่องสำอาง",
        "โต๊ะเครื่องแป้ง",
        "vanity table with ring light and mirror, person applying the product,"
        " vertical framing",
    ),
    "kitchen": Scene(
        "kitchen", "ครัว",
        "เคาน์เตอร์ครัว · เหมาะกับของใช้ในครัว อาหาร อาหารเสริม",
        "ครัว",
        "bright kitchen counter, person demonstrating the product,"
        " daylight from a window, vertical framing",
    ),
    "latenight": Scene(
        "latenight", "คุยดึก",
        "ไฟสลัว โทนอุ่น · เข้ากับน้ำเสียงคุยดึกชิล ๆ",
        "คุยดึก",
        "dim warm bedroom at night, bedside lamp glow, relaxed late night mood,"
        " vertical framing",
    ),
    "walking": Scene(
        "walking", "เดินพูด",
        "เดินไปพูดไป กล้องตามหน้า · จังหวะเร็ว ดูมีพลัง",
        "เดินพูด",
        "person walking and talking to a handheld camera, urban sidewalk,"
        " motion in the background, vertical framing",
    ),
}


def slug(name: str) -> str:
    return _UNSAFE.sub("-", name.strip()).strip("-").lower()


def get(scene_id: str | None) -> Scene | None:
    """ฉากที่ขอ — None หรือ 'auto' แปลว่าไม่เจาะจงฉาก ใช้ไฟล์ชั้นนอก"""
    return SCENES.get((scene_id or "").strip())


def folder_name(scene_id: str | None) -> str:
    """ชื่อโฟลเดอร์ย่อยที่จะไปหา — ว่างแปลว่าไม่ต้องหาโฟลเดอร์ย่อย"""
    scene = get(scene_id)
    return scene.folder if scene else ""


def choices() -> list[dict]:
    head = [{"id": AUTO, "name": "ไม่เจาะจงฉาก",
             "note": "ใช้ไฟล์ทั้งหมดในโฟลเดอร์ตัวละคร ไม่แยกฉาก"}]
    return head + [{"id": s.id, "name": s.name, "note": s.note} for s in SCENES.values()]


VALID = {AUTO, *SCENES}
